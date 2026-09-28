"""
build_summary_tables.py
---------------
End To End Supply Chain Performance Analytics Project
Author : Harshad

Purpose: Builds 4 persisted summary tables from the 6 raw tables:
  1. vendor_product_sales_summary   (purchases + sales + product_master)   grain: VendorNumber + ProductID
  2. procurement_logistics_summary  (purchases + vendor_invoice)          grain: PONumber
  3. store_city_performance_summary (purchases + sales + inventory-derived City lookup) grain: Store
  4. inventory_reconciliation_summary (begin_inventory + end_inventory + purchases + sales + product_master) grain: Store + ProductID

Dead-stock analysis and time-series analysis are NOT built here — they don't need new joins:
  - Dead stock  -> query vendor_product_sales_summary WHERE TotalSalesQuantity = 0
  - Time-series -> query the raw `sales` / `purchases` tables directly, grouped by date

Usage:
    python build_summary_tables.py

Requirements:
    - A `.env` file in the project root with the following variables:
        DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME
    - All 6 cleaned tables inside DB
    - ingestion_db script inside the project folder for importing ingest_table function
    - MySQL server running and accessible
    
Logs:
    All activity is written to logs/build_summary_tables.log
"""

from sqlalchemy import create_engine, text
import pandas as pd
import numpy as np
import logging
import time
import os
from dotenv import load_dotenv
from ingestion_db import ingest_table

# ---------------------------------------------------------------------------
# Logging setup
# Writes to both the console (so you can watch progress live)
# and to a log file (for a permanent record of every run).
# ---------------------------------------------------------------------------
logger = logging.getLogger('build_summary_tables')
logger.setLevel(logging.INFO)
os.makedirs('logs', exist_ok=True)  # relative to the working directory the script is run from

file_handler = logging.FileHandler('logs/build_summary_tables.log', mode='a')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# ---------------------------------------------------------------------------
# 1. VENDOR-PRODUCT SALES SUMMARY
# ---------------------------------------------------------------------------

def build_vendor_product_sales_summary(engine):
    """
    Vendor-product level sales summary. Grain: one row per (VendorNumber, ProductID).

      - Keeps unsold products (TotalSalesQuantity = 0) instead of filtering them out later;
        flags them with IsDeadStock instead of deleting the rows.
      - Keeps ProductID 2166 ("The Macallan Double Cask 12", EDRINGTON AMERICAS), which is
        received exclusively as free/promotional stock (PurchasePrice = 0 on all 153 of its
        purchase rows) but still generated ~$98K in real sales revenue. Flagged with
        IsFreeSample rather than silently dropped.
      - FreightCost is joined on VendorNumber only (freight isn't tracked per product in the
        source data) so it is intentionally repeated across every product row for that vendor.
        NEVER re-sum FreightCost when grouping this table further by vendor — it will be
        overcounted. Use a DISTINCT(VendorNumber, FreightCost) lookup for vendor-level freight.
      - Added BottleSizeCategory. A few products (4 out of 10,664) have more than one
        category value recorded, likely data-entry inconsistencies (e.g. a sampler
        pack sometimes logged as "Mini", sometimes as "Half Bottle"). Using MAX()
        instead of GROUP BY avoids splitting those products into duplicate
        (VendorNumber, ProductID) rows.
    """
    query = """
    WITH FreightSummary AS (
        SELECT VendorNumber, SUM(Freight) AS FreightCost
        FROM vendor_invoice
        GROUP BY VendorNumber
    ),
    PurchaseSummary AS (
        SELECT
            p.VendorNumber,
            p.VendorCanonicalName AS VendorName,
            p.ProductID,
            p.ProductName,
            p.PurchasePrice,
            pm.SalesPrice AS CatalogPrice,   -- static reference price from product_master, NOT actual avg selling price
            pm.Volume,
            pm.Classification,
            MAX(p.BottleSizeCategory) AS BottleSizeCategory,
            SUM(p.PurchaseQuantity) AS TotalPurchaseQuantity,
            SUM(p.PurchaseDollars)  AS TotalPurchaseDollars
        FROM purchases p
        JOIN product_master pm
            ON p.ProductID = pm.ProductID
        GROUP BY p.VendorNumber, p.VendorCanonicalName, p.ProductID, p.ProductName,
                 p.PurchasePrice, pm.SalesPrice, pm.Volume, pm.Classification
    ),
    SalesSummary AS (
        SELECT
            VendorNumber,
            ProductID,
            SUM(SalesQuantity) AS TotalSalesQuantity,
            SUM(SalesDollars)  AS TotalSalesDollars,
            SUM(ExciseTax)     AS TotalExciseTax,
            SUM(CASE WHEN IsFreePromotion = 1 THEN SalesQuantity ELSE 0 END) AS PromoSalesQuantity
        FROM sales
        GROUP BY VendorNumber, ProductID
    )
    SELECT
        ps.VendorNumber,
        ps.VendorName,
        ps.ProductID,
        ps.ProductName,
        ps.PurchasePrice,
        ps.CatalogPrice,
        ps.Volume,
        ps.Classification,
        ps.BottleSizeCategory,
        ps.TotalPurchaseQuantity,
        ps.TotalPurchaseDollars,
        ss.TotalSalesQuantity,
        ss.TotalSalesDollars,
        ss.TotalExciseTax,
        ss.PromoSalesQuantity,
        fs.FreightCost
    FROM PurchaseSummary ps
    LEFT JOIN SalesSummary ss
        ON ps.VendorNumber = ss.VendorNumber AND ps.ProductID = ss.ProductID
    LEFT JOIN FreightSummary fs
        ON ps.VendorNumber = fs.VendorNumber
    ORDER BY ps.TotalPurchaseDollars DESC
    """
    df = pd.read_sql_query(query, engine)

    # --- cleanup ---
    for col in ['TotalSalesQuantity', 'TotalSalesDollars', 'TotalExciseTax', 'FreightCost', 'PromoSalesQuantity']:
        df[col] = df[col].fillna(0)

    # --- derived columns ---
    df['IsDeadStock'] = (df['TotalSalesQuantity'] == 0).astype(int)
    df['IsFreeSample'] = (df['PurchasePrice'] == 0).astype(int)
    df['HasPromoSales'] = (df['PromoSalesQuantity'] > 0).astype(int)

    # revenue-weighted avg selling price, NaN where never sold.
    df['AvgSellingPrice'] = np.where(
        df['TotalSalesQuantity'] > 0,
        df['TotalSalesDollars'] / df['TotalSalesQuantity'],
        np.nan
    )

    df['GrossProfit'] = df['TotalSalesDollars'] - df['TotalPurchaseDollars']

    # ProfitMargin left as NaN (not 0) when there's no revenue, so "no sales" isn't confused with "0% margin"
    df['ProfitMargin'] = np.where(
        df['TotalSalesDollars'] > 0,
        (df['GrossProfit'] / df['TotalSalesDollars']) * 100,
        np.nan
    )

    df['StockTurnover'] = np.where(
        df['TotalPurchaseQuantity'] > 0,
        df['TotalSalesQuantity'] / df['TotalPurchaseQuantity'],
        np.nan
    )

    df['SalesToPurchaseRatio'] = np.where(
        df['TotalPurchaseDollars'] > 0,
        df['TotalSalesDollars'] / df['TotalPurchaseDollars'],
        np.nan
    )

    return df


# ---------------------------------------------------------------------------
# 2. PROCUREMENT & LOGISTICS SUMMARY
# ---------------------------------------------------------------------------

def build_procurement_logistics_summary(engine):
    """
    Procurement and logistics summary. Grain: one row per PONumber.

    Note: vendor_invoice's own Quantity/POTotalAmount do NOT reliably reconcile with
    the sum of purchases.PurchaseQuantity/PurchaseDollars for the same PO — so both are kept
    side by side (Invoice* vs Purchases*) rather than assumed interchangeable, and a
    QuantityMismatch flag is added to see where they disagree.
    """
    query = """
    SELECT
        vi.VendorNumber,
        vi.VendorCanonicalName AS VendorName,
        vi.PONumber,
        vi.PODate,
        vi.InvoiceDate,
        vi.PayDate,
        vi.PaymentLagDays,
        vi.Quantity        AS InvoiceQuantity,
        vi.POTotalAmount,
        vi.Freight,
        vi.LandedCost,
        ps.TotalPurchaseQuantity,
        ps.TotalPurchaseDollars,
        ps.MinReceivingDate,
        ps.MaxReceivingDate
    FROM vendor_invoice vi
    LEFT JOIN (
        SELECT
            PONumber,
            VendorNumber,
            SUM(PurchaseQuantity) AS TotalPurchaseQuantity,
            SUM(PurchaseDollars)  AS TotalPurchaseDollars,
            MIN(ReceivingDate)    AS MinReceivingDate,
            MAX(ReceivingDate)    AS MaxReceivingDate
        FROM purchases
        GROUP BY PONumber, VendorNumber
    ) ps
        ON vi.PONumber = ps.PONumber AND vi.VendorNumber = ps.VendorNumber
    """
    df = pd.read_sql_query(query, engine)

    for col in ['TotalPurchaseQuantity', 'TotalPurchaseDollars']:
        df[col] = df[col].fillna(0)

    for col in ['PODate', 'InvoiceDate', 'PayDate', 'MinReceivingDate', 'MaxReceivingDate']:
        df[col] = pd.to_datetime(df[col])

    # --- derived logistics metrics ---
    df['FreightPctOfPurchase'] = np.where(
        df['POTotalAmount'] > 0,
        (df['Freight'] / df['POTotalAmount']) * 100,
        np.nan
    )

    df['OrderToReceiveDays']   = (df['MinReceivingDate'] - df['PODate']).dt.days
    df['ReceiveToInvoiceDays'] = (df['InvoiceDate'] - df['MaxReceivingDate']).dt.days
    df['InvoiceToPayDays']     = (df['PayDate'] - df['InvoiceDate']).dt.days
    df['QuantityMismatch'] = (df['InvoiceQuantity'] != df['TotalPurchaseQuantity']).astype(int)

    return df


# ---------------------------------------------------------------------------
# 3. STORE / CITY PERFORMANCE SUMMARY
# ---------------------------------------------------------------------------

def build_store_city_performance_summary(engine):
    """
    One row per store. City is not in the purchases or sales tables, it only exists in the inventory tables. Since every store maps to exactly one city (no store belongs to two cities), and every store in purchases also appears in the inventory tables, we can safely use the inventory table as a simple lookup to get the city for each store, instead of doing a complex join.
    """
    query = """
    WITH StoreCity AS (
        SELECT DISTINCT Store, City FROM begin_inventory
        UNION
        SELECT DISTINCT Store, City FROM end_inventory
    ),
    PurchaseByStore AS (
        SELECT
            Store,
            SUM(PurchaseQuantity) AS TotalPurchaseQuantity,
            SUM(PurchaseDollars)  AS TotalPurchaseDollars
        FROM purchases
        GROUP BY Store
    ),
    SalesByStore AS (
        SELECT
            Store,
            SUM(SalesQuantity) AS TotalSalesQuantity,
            SUM(SalesDollars)  AS TotalSalesDollars,
            SUM(ExciseTax)     AS TotalExciseTax,
            SUM(CASE WHEN IsFreePromotion = 1 THEN SalesQuantity ELSE 0 END) AS PromoSalesQuantity
        FROM sales
        GROUP BY Store
    )
    SELECT
        sc.Store,
        sc.City,
        pbs.TotalPurchaseQuantity,
        pbs.TotalPurchaseDollars,
        sbs.TotalSalesQuantity,
        sbs.TotalSalesDollars,
        sbs.TotalExciseTax,
        sbs.PromoSalesQuantity
    FROM StoreCity sc
    LEFT JOIN PurchaseByStore pbs ON sc.Store = pbs.Store
    LEFT JOIN SalesByStore sbs ON sc.Store = sbs.Store
    """
    df = pd.read_sql_query(query, engine)

    for col in ['TotalPurchaseQuantity', 'TotalPurchaseDollars',
                'TotalSalesQuantity', 'TotalSalesDollars', 'TotalExciseTax', 'PromoSalesQuantity']:
        df[col] = df[col].fillna(0)

    df['GrossProfit'] = df['TotalSalesDollars'] - df['TotalPurchaseDollars']
    df['ProfitMargin'] = np.where(
        df['TotalSalesDollars'] > 0,
        (df['GrossProfit'] / df['TotalSalesDollars']) * 100,
        np.nan
    )
    df['AvgSellingPrice'] = np.where(
        df['TotalSalesQuantity'] > 0,
        df['TotalSalesDollars'] / df['TotalSalesQuantity'],
        np.nan
    )

    df['StockTurnover'] = np.where(
        df['TotalPurchaseQuantity'] > 0,
        df['TotalSalesQuantity'] / df['TotalPurchaseQuantity'],
        np.nan
    )

    df['SalesToPurchaseRatio'] = np.where(
        df['TotalPurchaseDollars'] > 0,
        df['TotalSalesDollars'] / df['TotalPurchaseDollars'],
        np.nan
    )

    return df


# ---------------------------------------------------------------------------
# 4. INVENTORY RECONCILIATION SUMMARY
# ---------------------------------------------------------------------------

def build_inventory_reconciliation_summary(engine):
    """
    Inventory reconciliation summary. Grain: one row per (Store, ProductID).

      - MySQL has no FULL OUTER JOIN, so begin_inventory and end_inventory are combined
        using a LEFT JOIN plus a reversed LEFT JOIN, joined together with UNION. This gives
        every product that appears in either snapshot, even if only in one of them.
      - VendorNumber and Classification are pulled from product_master. Safe to do here
        since product_master is a static reference table, not a transaction table, so
        joining it in doesn't distort any numbers.
      - ExpectedEndQty = BeginQty + TotalPurchaseQuantity (over the year) - TotalSalesQuantity
        (over the year). Variance = EndQty - ExpectedEndQty. A nonzero Variance flags a
        potential issue: shrinkage, breakage, theft, or a data problem.
      - IsNewProduct flags products in end_inventory but not begin_inventory.
        IsDiscontinued flags products in begin_inventory but not end_inventory.
      - For the reconciliation math to be valid, purchases/sales must cover exactly the same
        window as the two inventory snapshots (Jan 1 - Dec 31, 2024). Verified: ReceivingDate
        in purchases and SalesDate in sales are both strictly within 2024, matching begin
        (Jan 1) and end (Dec 31) exactly — so summing purchase/sales quantities with no
        extra date filter beyond the explicit BETWEEN bounds below is correct.
      - PODate is deliberately NOT used for the date filter. Some orders were placed in late
        December 2023 but received in January 2024 — filtering on PODate instead of
        ReceivingDate would wrongly exclude stock that physically entered inventory in 2024.
      - VendorCanonicalName doesn't exist directly in product_master, so it's pulled from
        purchases (DISTINCT VendorNumber -> VendorCanonicalName) and joined in separately.
    """
    query = """
    WITH VendorLookup AS (
        SELECT DISTINCT VendorNumber, VendorCanonicalName
        FROM purchases
    ),
    InventoryUnion AS (
        SELECT
            b.Store, b.ProductID,
            b.StockQuantity AS BeginQty, b.SalesPrice AS BeginSalesPrice,
            e.StockQuantity AS EndQty,   e.SalesPrice AS EndSalesPrice
        FROM begin_inventory b
        LEFT JOIN end_inventory e
            ON b.Store = e.Store AND b.ProductID = e.ProductID
        UNION
        SELECT
            e.Store, e.ProductID,
            b.StockQuantity AS BeginQty, b.SalesPrice AS BeginSalesPrice,
            e.StockQuantity AS EndQty,   e.SalesPrice AS EndSalesPrice
        FROM end_inventory e
        LEFT JOIN begin_inventory b
            ON b.Store = e.Store AND b.ProductID = e.ProductID
        WHERE b.Store IS NULL
    ),
    PurchQty AS (
        SELECT Store, ProductID, SUM(PurchaseQuantity) AS TotalPurchaseQuantity
        FROM purchases
        WHERE ReceivingDate BETWEEN '2024-01-01' AND '2024-12-31'
        GROUP BY Store, ProductID
    ),
    SalesQty AS (
        SELECT Store, ProductID, SUM(SalesQuantity) AS TotalSalesQuantity
        FROM sales
        WHERE SalesDate BETWEEN '2024-01-01' AND '2024-12-31'
        GROUP BY Store, ProductID
    )
    SELECT
        iu.Store,
        iu.ProductID,
        pm.ProductName,
        pm.VendorNumber,
        vl.VendorCanonicalName AS VendorName,
        pm.Classification,
        iu.BeginQty,
        iu.EndQty,
        iu.BeginSalesPrice,
        iu.EndSalesPrice,
        pq.TotalPurchaseQuantity,
        sq.TotalSalesQuantity
    FROM InventoryUnion iu
    LEFT JOIN product_master pm ON iu.ProductID = pm.ProductID
    LEFT JOIN VendorLookup vl ON pm.VendorNumber = vl.VendorNumber
    LEFT JOIN PurchQty pq ON iu.Store = pq.Store AND iu.ProductID = pq.ProductID
    LEFT JOIN SalesQty sq ON iu.Store = sq.Store AND iu.ProductID = sq.ProductID
    """
    df = pd.read_sql_query(query, engine)

    df['BeginQty'] = df['BeginQty'].fillna(0)
    df['EndQty'] = df['EndQty'].fillna(0)
    df['TotalPurchaseQuantity'] = df['TotalPurchaseQuantity'].fillna(0)
    df['TotalSalesQuantity'] = df['TotalSalesQuantity'].fillna(0)

    df['IsNewProduct']    = df['BeginSalesPrice'].isna().astype(int)   # absent from begin_inventory
    df['IsDiscontinued']  = df['EndSalesPrice'].isna().astype(int)     # absent from end_inventory

    df['ExpectedEndQty'] = df['BeginQty'] + df['TotalPurchaseQuantity'] - df['TotalSalesQuantity']
    df['Variance'] = df['EndQty'] - df['ExpectedEndQty']

    return df


# ---------------------------------------------------------------------------
# TABLE CREATION
# ---------------------------------------------------------------------------

def create_tables(engine):
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS vendor_product_sales_summary"))
        conn.execute(text("""CREATE TABLE vendor_product_sales_summary (
            VendorNumber BIGINT,
            VendorName VARCHAR(255),
            ProductID BIGINT,
            ProductName VARCHAR(150),
            PurchasePrice DECIMAL(10,2),
            CatalogPrice DECIMAL(10,2),
            Volume INT UNSIGNED,
            Classification BIGINT,
            BottleSizeCategory VARCHAR(50),
            TotalPurchaseQuantity INT,
            TotalPurchaseDollars DECIMAL(15,2),
            TotalSalesQuantity INT,
            TotalSalesDollars DECIMAL(15,2),
            TotalExciseTax DECIMAL(15,2),
            FreightCost DECIMAL(15,2),
            IsDeadStock TINYINT,
            IsFreeSample TINYINT,
            PromoSalesQuantity INT,
            HasPromoSales TINYINT,
            AvgSellingPrice DECIMAL(10,2),
            GrossProfit DECIMAL(15,2),
            ProfitMargin DECIMAL(10,2),
            StockTurnover DECIMAL(10,4),
            SalesToPurchaseRatio DECIMAL(10,4),
            PRIMARY KEY (VendorNumber, ProductID)
        )"""))

        conn.execute(text("DROP TABLE IF EXISTS procurement_logistics_summary"))
        conn.execute(text("""CREATE TABLE procurement_logistics_summary (
            VendorNumber BIGINT,
            VendorName VARCHAR(255),
            PONumber BIGINT,
            PODate DATE,
            InvoiceDate DATE,
            PayDate DATE,
            PaymentLagDays INT,
            InvoiceQuantity BIGINT,
            POTotalAmount DECIMAL(10,2),
            Freight DECIMAL(10,2),
            LandedCost DECIMAL(10,2),
            TotalPurchaseQuantity INT,
            TotalPurchaseDollars DECIMAL(15,2),
            MinReceivingDate DATE,
            MaxReceivingDate DATE,
            FreightPctOfPurchase DECIMAL(10,4),
            OrderToReceiveDays INT,
            ReceiveToInvoiceDays INT,
            InvoiceToPayDays INT,
            QuantityMismatch TINYINT,
            PRIMARY KEY (PONumber)
        )"""))

        conn.execute(text("DROP TABLE IF EXISTS store_city_performance_summary"))
        conn.execute(text("""CREATE TABLE store_city_performance_summary (
            Store BIGINT,
            City VARCHAR(100),
            TotalPurchaseQuantity INT,
            TotalPurchaseDollars DECIMAL(15,2),
            TotalSalesQuantity INT,
            TotalSalesDollars DECIMAL(15,2),
            TotalExciseTax DECIMAL(15,2),
            PromoSalesQuantity INT,
            GrossProfit DECIMAL(15,2),
            ProfitMargin DECIMAL(10,2),
            AvgSellingPrice DECIMAL(10,2),
            StockTurnover DECIMAL(10,4),
            SalesToPurchaseRatio DECIMAL(10,4),
            PRIMARY KEY (Store)
        )"""))

        conn.execute(text("DROP TABLE IF EXISTS inventory_reconciliation_summary"))
        conn.execute(text("""CREATE TABLE inventory_reconciliation_summary (
            Store BIGINT,
            ProductID BIGINT,
            ProductName VARCHAR(150),
            VendorNumber BIGINT,
            VendorName VARCHAR(255),
            Classification BIGINT,
            BeginQty BIGINT,
            EndQty BIGINT,
            BeginSalesPrice DECIMAL(10,2),
            EndSalesPrice DECIMAL(10,2),
            TotalPurchaseQuantity INT,
            TotalSalesQuantity INT,
            IsNewProduct TINYINT,
            IsDiscontinued TINYINT,
            ExpectedEndQty INT,
            Variance INT,
            PRIMARY KEY (Store, ProductID)
        )"""))

        conn.commit()
    logger.info("All 4 summary tables created successfully")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    load_dotenv()

    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT')
    DB_NAME = os.getenv('DB_NAME')

    engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    logger.info('Creating empty summary tables......')
    create_tables(engine)

    builders = [
        ('vendor_product_sales_summary', build_vendor_product_sales_summary),
        ('procurement_logistics_summary', build_procurement_logistics_summary),
        ('store_city_performance_summary', build_store_city_performance_summary),
        ('inventory_reconciliation_summary', build_inventory_reconciliation_summary),
    ]

    succeeded, failed = [], []

    for table_name, builder_fn in builders:
        try:
            logger.info(f'Building {table_name}......')
            start = time.time()
            df = builder_fn(engine)
            logger.info(f'{table_name} built in {round(time.time() - start, 2)}s, {len(df)} rows')

            start = time.time()
            ingest_table(df, table_name, engine, mode='append', chunksize=5000)
            logger.info(f'{table_name} ingested in {round(time.time() - start, 2)}s')
            succeeded.append(table_name)
        except Exception as e:
            # The 4 tables are independent, so one failing shouldn't block the others.
            # This catches any error (bad query, DB issue, etc.)
            # so one failure doesn't stop the whole script.
            logger.error(f'{table_name} failed: {e}')
            failed.append(table_name)

    logger.info(f'Run complete. Succeeded: {succeeded}. Failed: {failed}')
    print(f"Done. Succeeded: {succeeded}")
    if failed:
        print(f"Failed (see logs/build_summary_tables.log for details): {failed}")
