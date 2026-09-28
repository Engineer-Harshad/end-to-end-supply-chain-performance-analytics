"""
ingestion_db.py
---------------
End to end supply chain Performance Analytics Project
Author : Harshad
Purpose: Reads all raw CSV files from the `data/` folder and loads them into
         a MySQL database as individual tables (one table per CSV file).

This is the first step in the data pipeline:
    Raw CSVs (data/) --> MySQL database (vendor_data)

Tables loaded:
    - begin_inventory
    - end_inventory
    - purchase_prices
    - purchases
    - sales
    - vendor_invoice

Usage:
    python ingestion_db.py

Requirements:
    - A `.env` file in the project root with the following variables:
        DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
    - All raw CSV files placed inside the `data/` folder
    - MySQL server running and accessible

Logs:
    All activity is written to logs/ingestion_db.log
"""

import os
import logging
import time

import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
load_dotenv()

DB_USER     = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST     = os.getenv('DB_HOST')
DB_PORT     = os.getenv('DB_PORT')
DB_NAME     = os.getenv('DB_NAME')

# ---------------------------------------------------------------------------
# Logging setup
# Writes to both the console (so you can watch progress live)
# and to a log file (for a permanent record of every run).
# ---------------------------------------------------------------------------
os.makedirs('logs', exist_ok=True)

logger = logging.getLogger('ingestion_db')
logger.setLevel(logging.DEBUG)

# File handler - appends to log file on every run
file_handler = logging.FileHandler('logs/ingestion_db.log', mode='a')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Stream handler - prints to console so you can see progress live
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def create_database():
    """
    Creates the MySQL database if it does not already exist.
    Connects without specifying a database first (no DB in the URL),
    then runs CREATE DATABASE IF NOT EXISTS.
    """
    try:
        temp_engine = create_engine(
            f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/"
        )
        with temp_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}"))
            conn.commit()
        logger.info(f"Database '{DB_NAME}' is ready.")
    except Exception as e:
        logger.error(f"Failed to create database '{DB_NAME}': {e}")
        raise


def ingest_table(df, table_name, engine, mode, chunksize):
    """
    Loads a single DataFrame into a MySQL table.
    """
    try:
        start = time.time()
        df.to_sql(
            table_name,
            con=engine,
            if_exists=mode,   
            index=False,
            chunksize=chunksize,
            method='multi'
        )
        elapsed = round(time.time() - start, 2)
        logger.info(f"Loaded '{table_name}': {len(df):,} rows in {elapsed}s")
    except Exception as e:
        logger.error(f"Failed to load table '{table_name}': {e}")
        raise


def load_raw_data(engine, data_dir, if_exists, chunksize):
    """
    Scans the `data/` folder for CSV files and loads each one into MySQL.
    Table name is derived from the filename (without the .csv extension).
    """
    if not os.path.exists(data_dir):
        logger.error(f"Data folder '{data_dir}' not found. Aborting.")
        raise FileNotFoundError(f"'{data_dir}' folder does not exist.")

    # Only pick files that end exactly with .csv
    csv_files = [f for f in os.listdir(data_dir) if f.endswith('.csv')]

    if not csv_files:
        logger.warning(f"No CSV files found in '{data_dir}'. Nothing to load.")
        return

    logger.info(f"Found {len(csv_files)} CSV file(s) in '{data_dir}': {csv_files}")

    pipeline_start = time.time()

    for file in csv_files:
        file_path = os.path.join(data_dir, file)
        table_name = file[:-4]  # strip .csv to get table name

        try:
            logger.info(f"Reading '{file}' in chunks...")
            first_chunk = True
            # Read and ingest the file chunk by chunk (10k rows at a time)
            for df_chunk in pd.read_csv(file_path, chunksize=chunksize):
                mode = if_exists if first_chunk else 'append'
                ingest_table(df_chunk, table_name, engine, mode, chunksize)
                first_chunk = False
                
            logger.info(f"Successfully loaded '{table_name}' completely.")

        except Exception as e:
            # Log the error and continue with remaining files
            logger.error(f"Skipping '{file}' due to error: {e}")
            continue

    total_time = round((time.time() - pipeline_start) / 60, 2)
    logger.info('--------- Ingestion complete ---------')
    logger.info(f'Total time: {total_time} minutes')


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    logger.info('========= Starting ingestion pipeline =========')

    # Step 1: Create database if it does not exist
    create_database()

    # Step 2: Connect to the target database
    engine = create_engine(
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    # Step 3: Load all CSVs from the data/ folder into MySQL
    # if_exists='replace' drops and recreates each table on every run
    load_raw_data(engine, data_dir='data', if_exists='replace', chunksize=10_000)

    logger.info('========= Pipeline finished =========')
