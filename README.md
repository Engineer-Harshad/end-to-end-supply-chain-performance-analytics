# End-to-End Supply Chain Performance Analytics

An end-to-end data analytics project built to understand **vendor profitability, procurement and logistics, sales trends, store performance, inventory movement, and dead stock**.

The project works with 2024 supply-chain data and follows a complete pipeline:

**Raw CSV files → MySQL → Data Cleaning → Summary Tables → Analysis → Business Findings**

The main goal was not just to create charts, but to build a reusable data pipeline and use the cleaned data to answer practical business questions.

The analysis covers **January 1, 2024 through December 31, 2024**.

---

## Project Overview

This project analyzes six main areas:

1. **Vendor & product profitability**
2. **Procurement & logistics**
3. **Sales and time-series trends**
4. **Store and city performance**
5. **Inventory reconciliation**
6. **Dead stock and unsold inventory**

The analysis combines SQL, Python, pandas, MySQL, statistical testing, and visualization.

The final analysis is based on cleaned data stored in MySQL and uses four summary tables to avoid repeating large joins during analysis.

---

## Business Questions

The project tries to answer questions such as:

- Which vendors contribute the most purchases, sales, and profit?
- Which vendor-product combinations are profitable or loss-making?
- How does stock turnover relate to vendor profitability?
- Does buying larger quantities result in a lower observed unit purchase price?
- How much inventory capital is sitting unsold?
- How important are freight costs?
- How long does it take to receive, invoice, and pay for purchase orders?
- Are there purchase orders with invoices but no matching receiving records?
- When are sales highest and lowest during the year?
- Which stores perform well or poorly?
- How much inventory growth happened during 2024?
- Do beginning inventory + purchases - sales reconcile with ending inventory?
- Which products are dead stock company-wide?
- Which products are not selling at individual stores even though they may sell elsewhere?

---

## Data Used

The project starts with six CSV files:

| File | Purpose |
|---|---|
| `begin_inventory.csv` | Beginning inventory snapshot |
| `end_inventory.csv` | Ending inventory snapshot |
| `purchase_prices.csv` | Product and reference pricing information |
| `purchases.csv` | Purchase and receiving transactions |
| `sales.csv` | Sales transactions |
| `vendor_invoice.csv` | Purchase-order/invoice level information including freight |

---

## Project Pipeline

```text
                    RAW DATA
                       │
                       ▼
              Six CSV files in data/
                       │
                       ▼
               ingestion_db.py
                       │
                       ▼
                  MySQL DB
                 vendor_data
                       │
                       ▼
              data_cleaning.ipynb
                       │
                       ▼
             Cleaned MySQL tables
                       │
                       ▼
            build_summary_tables.py
                       │
                       ▼
              Four summary tables
                       │
                       ▼
          supply_chain_analysis.ipynb
                       │
                       ▼
        Business analysis & findings
```

---

## 1. Data Ingestion

`ingestion_db.py` is the first step in the pipeline.

It:

- Creates the MySQL database if it does not already exist.
- Reads all CSV files from the `data/` folder.
- Loads the files into MySQL.
- Uses chunked reading so the large sales and purchase files can be handled efficiently.
- Creates a log file at `logs/ingestion_db.log`.

---

## 2. Data Cleaning

The cleaning work is documented in `data_cleaning.ipynb`.

Most cleaning is performed directly in MySQL using SQL. Pandas is mainly used to read the database and inspect results.

## 3. Summary Tables

`build_summary_tables.py` creates four analytical tables from the cleaned data.

### `vendor_product_sales_summary`

**Grain:** one row per `VendorNumber + ProductID`

Combines purchases, sales, product master information, and vendor freight.

Unsold products are intentionally retained and flagged as dead stock instead of being removed.

**Important:** freight is vendor-level data and is repeated across product rows. It should not be summed again after grouping this table by vendor without first removing duplicates.

### `procurement_logistics_summary`

**Grain:** one row per `PONumber`

Combines purchase-order/invoice information with purchase/receiving data.

Invoice quantities and purchase quantities are kept separately because missing purchase records exist.

### `store_city_performance_summary`

**Grain:** one row per `Store`

Combines purchase performance, sales performance, and city information from inventory.

### `inventory_reconciliation_summary`

**Grain:** one row per `Store + ProductID`

It calculates:

```text
ExpectedEndQty =
BeginningQty
+ Purchases
- Sales

Variance =
ActualEndQty
- ExpectedEndQty
```

It also identifies new and discontinued products.

For reconciliation, purchases are filtered using `ReceivingDate` and sales using `SalesDate`, so the calculation follows the physical movement of stock during 2024.

---

## 4. Main Analysis

The detailed analysis is in `supply_chain_analysis.ipynb`.

### Vendor and Product Profitability

Overall 2024 results:

| Metric | Result |
|---|---:|
| Total purchases | **$321.90M** |
| Total sales | **$451.72M** |
| Gross profit | **$129.82M** |
| Sales-weighted profit margin | **28.74%** |

The analysis uses **sales-weighted margins** when comparing groups. This avoids giving a tiny product and a major product the same importance.

The analysis covers **January 1, 2024 through December 31, 2024**.

### Low-Selling Products

Vendor-product rows in the bottom 25% by sales have a sales-weighted margin of approximately **-87.5%**, compared with about **29.3%** for the top 25%.

A Mann-Whitney U test was also used:

```text
p-value = 3.033e-70
```

The analysis therefore finds a statistically significant difference between the two margin distributions.

### Pricing

About **26.6%** of products with sales had an actual average selling price that differed from the catalog price by more than $1.

The correlation between actual average selling price and total sales was approximately **-0.015**, so the analysis does not show a meaningful relationship between higher selling price and higher sales.

### Bulk Purchasing

Average observed unit purchase price by purchase-volume group:

| Order-size group | Average unit purchase price |
|---|---:|
| Small | $43.78 |
| Medium | $17.89 |
| Large | $11.31 |

The unit price decreases as purchase quantity increases. However, this does **not** prove a volume-discount effect because product mix also changes with order size.

---

## 5. Procurement and Logistics

The project analyzes **5,543 purchase orders** across **126 vendors**.

### Freight

Average freight is approximately **0.55% of PO value**.

Freight amount has a correlation of **0.985** with PO size, which is expected because larger purchase orders generally have larger freight amounts.

For vendor comparison, freight percentage is more useful than absolute freight dollars.

### Lead Times

For the **4,207 POs with receiving records**, median timing is:

| Stage | Median |
|---|---:|
| Order → Receive | **6 days** |
| Receive → Invoice | **7 days** |
| Invoice → Pay | **35 days** |

Average lead time and lead-time consistency are different things. The correlation between average lead time and lead-time variability is about **0.49**.

### Missing Purchase/Receiving Records

One of the most important data-quality findings is:

- **1,336 of 5,543 POs (24.1%)** have no matching purchase/receiving record.
- These POs represent **$90.31M**, or **28.1% of all invoiced dollars**.
- Among POs that do have purchase records, **0** had a different invoice quantity.

Possible explanations include goods not yet received, missing receiving data, invoices recorded before receipts, or an incomplete source-data extract. The analysis does not treat any one explanation as proven.

---

## 6. Sales and Time-Series Analysis

Time-series analysis is performed directly on the raw `sales` and `purchases` tables because the summary tables collapse the full year into higher-level grains.

### Monthly sales

- Highest sales month: **December — $52.31M**
- Lowest sales month: **February — $28.88M**

### Receiving pattern

Very little stock is received on Tuesdays and Wednesdays, while the other days generally average around **$0.9M–$1.4M** in receiving value.

This should be compared with the actual delivery schedule before treating it as an operational problem.

### Promotions

Promotional/free sales are extremely rare:

- **55 promotional transactions**
- out of approximately **12.8M sales rows**
- **103 promotional units**

There are too few promotional transactions to reliably measure their effect on sales.

---

## 7. Store and City Performance

The project contains:

- **80 stores**
- **68 cities**

Store performance varies considerably.

Store size does not show a strong relationship with profit margin, so stores should be reviewed individually rather than judged mainly by their size.

### Store 81

Store 81 is a notable case:

- Profit margin is about **-86%**
- **3,589** new products
- **0** discontinued products

The stores with the most new products are also concentrated among the lower-margin stores. This may be consistent with new or expanding stores building opening inventory before sales catch up, but it should be checked against actual store-opening records.

City-level comparisons should also consider the number and size of stores in each city.

---

## 8. Inventory Reconciliation

Total inventory increased during 2024:

| Metric | Units |
|---|---:|
| Beginning inventory | 4,219,275 |
| Ending inventory | 4,885,776 |
| Net increase | **666,501** |
| Percentage increase | **15.8%** |

The reconciliation is:

```text
Expected Ending Inventory
= Beginning Inventory
+ Purchases
- Sales
```

Results:

- **254,835** Store-Product records analyzed
- **254,810** reconcile exactly
- **25** have a non-zero variance
- Exact reconciliation rate: **99.9902%**
- Total variance: **207 units**
- Largest single variance: **36 units**

All 25 variances are positive.

Several of the largest exceptions have negative expected ending inventory, which may point to missing receipts or other stock movements in the purchase data rather than straightforward shrinkage.

Because the reconciliation is unusually clean, the logic should be retested on another real-world data source.

---

## 9. Product Churn and Price Movement

During the year:

- **48,306** products appear only in ending inventory.
- **30,346** products appear only in beginning inventory.

For Store-Product combinations where both beginning and ending prices were available:

- **176,183** combinations had both prices.
- **29.6%** had a price change.
- Changes ranged from **-$40 to +$260**.

---

## 10. Unsold Inventory and Dead Stock

There are two different concepts used in the project.

### Broader unsold inventory

The analysis estimates approximately **$15.60M** of purchase capital tied up in unsold surplus.

### Company-wide dead stock

Dead stock is defined as a product that was purchased but had **zero sales anywhere in the company during 2024**.

Results:

- **178** dead vendor-product lines
- **1.7%** of 10,693 vendor-product lines
- **$282.18K** of purchase capital
- About **0.09%** of total purchases

### Store-level dead stock

A product can sell well across the company but still have zero sales at a specific store.

The analysis found:

- **178** products dead company-wide
- **2,556** products unsold at least at one store
- **2,378** of those were locally dead but not company-wide dead

This suggests that store-level product allocation and transfers may be useful areas to investigate.

---

## Important Data Notes

A few points are important when interpreting the results:

- `Classification` is present in the data, but its business meaning is not documented. The large margin difference between classifications should therefore be interpreted carefully.
- Dead stock and broader unsold inventory are different measures.
- Freight in `vendor_product_sales_summary` is vendor-level freight repeated across product rows. It must not be summed again at vendor level without removing duplicates.
- The product-level catalog price is a reference price, while `AvgSellingPrice` is calculated from actual sales.
- The lower unit purchase price for large purchase quantities does not prove that suppliers provide volume discounts; product mix can explain part of the pattern.
- Store-level dead stock is measured by product count, not by the dollar value of the stock.
- Promotional sales are too rare in this dataset to support a reliable effectiveness analysis.
- Inventory reconciliation is unusually clean and should be validated against another real-world source.

---

## Key Findings at a Glance

| Area | Finding |
|---|---|
| Overall margin | **28.74%** sales-weighted margin |
| Vendor concentration | Top 10 vendors = **65.3%** of purchases |
| Unsold inventory | **$15.60M** |
| Company-wide dead stock | **178 products / $282.18K** |
| Store-level unsold products | **2,556 products** |
| Missing purchase records | **1,336 of 5,543 POs** |
| Value of unmatched invoices | **$90.31M** |
| Inventory growth | **15.8%** |
| Inventory reconciliation | **99.9902% exact** |
| Highest sales month | **December — $52.31M** |
| Lowest sales month | **February — $28.88M** |
| Promotional transactions | **55** |

---

## Project Structure

```text
end-to-end-supply-chain-performance-analytics/
│
├── data/
│   ├── begin_inventory.csv
│   ├── end_inventory.csv
│   ├── purchase_prices.csv
│   ├── purchases.csv
│   ├── sales.csv
│   └── vendor_invoice.csv
│
├── logs/
│   ├── ingestion_db.log
│   └── build_summary_tables.log
│
├── data_cleaning.ipynb
├── supply_chain_analysis.ipynb
├── ingestion_db.py
├── build_summary_tables.py
├── open_jupyter.bat
├── .env
├── .gitignore
├── README.md
└── project_report/
    └── Supply_Chain_Analytics_Project_Report.pdf
```

> **GitHub note:** the raw CSV files are large, so they are not pushed to GitHub because of GitHub file-size limits.

---

## How to Run the Project

### 1. Install Python packages

```bash
pip install pandas numpy sqlalchemy pymysql python-dotenv scipy matplotlib seaborn jupyter
```

### 2. Set up MySQL

Make sure MySQL is installed, running, and accessible.

Create a `.env` file in the project root:

```env
DB_USER=your_mysql_username
DB_PASSWORD=your_mysql_password
DB_HOST=localhost
DB_PORT=3306
DB_NAME=vendor_data
```

### 3. Put the CSV files inside `data/`

```text
data/
```

### 4. Run data ingestion

```bash
python ingestion_db.py
```

This creates the database if needed and loads the raw CSV files into MySQL.

### 5. Run data cleaning

Open:

```text
data_cleaning.ipynb
```

Run the notebook from top to bottom.

The cleaning notebook modifies the MySQL tables, so the sections should not be rerun after they have already been successfully applied unless the database has been recreated.

### 6. Build analytical summary tables

```bash
python build_summary_tables.py
```

This creates:

```text
vendor_product_sales_summary
procurement_logistics_summary
store_city_performance_summary
inventory_reconciliation_summary
```

### 7. Run the final analysis

Open:

```text
supply_chain_analysis.ipynb
```

Run the notebook to reproduce the analysis, tables, statistical test, visualizations, and findings.

---

## Logs

The pipeline writes progress and error information to:

```text
logs/ingestion_db.log
logs/build_summary_tables.log
```

The summary-table script also prints progress to the console while it runs.

---

## Tools and Technologies

- **Python**
- **MySQL**
- **SQL**
- **Pandas**
- **NumPy**
- **SQLAlchemy**
- **PyMySQL**
- **SciPy**
- **Matplotlib**
- **Seaborn**
- **Jupyter Notebook**
- **python-dotenv**

## Final Takeaway

The analysis shows a profitable business overall, with a **28.74% sales-weighted gross margin**, but also highlights several areas that deserve attention:

- vendor concentration
- low-selling and loss-making product lines
- unsold inventory
- store-level product allocation
- missing purchase/receiving records
- supplier lead-time consistency
- store-level performance differences
- inventory reconciliation exceptions

The project also shows that many operational patterns can be measured directly from transactional data, including seasonality, receiving schedules, product churn, price movement, vendor performance, and inventory flow.

The next step after the analysis would be to validate the flagged cases with the relevant operational teams and use the results to prioritize specific vendor, store, procurement, and inventory actions.

---

## Author

**Harshad Deshmukh**

End-to-End Data Analytics Project
