import base64
import os
import json
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv
from google.cloud import bigquery

## Data source info
load_dotenv('.env')
api_key = os.getenv(key='RAPIDAPI_API_KEY')
host = 'bloomberg-market-and-financial-news.p.rapidapi.com'

companies = [
    ("facebook", "meta:us"),
    ("amazon", "amzn:us"),
    ("apple", "aapl:us"),
    ("netflix", "nflx:us"),
    ("google", "googl:us"),
    ("microsoft", "msft:us"),
]

## Data destination info
BQ_PROJECT_DATASET_TABLE = os.getenv(key='BQ_PROJECT_DATASET_TABLE')


def get_stat(name: str) -> dict:
    """ Get stock statistics from bloomberg api """

    data = {}
    data['stock'] = name

    url = "https://bloomberg-market-and-financial-news.p.rapidapi.com/stock/get-statistics"
    querystring = {
        "id":name,
        "template":"STOCK"
    }
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "bloomberg-market-and-financial-news.p.rapidapi.com"
    }

    response = requests.request(
        "GET", url, headers=headers, params=querystring, timeout=15
    ).json()

    for info in response['result'][0]['table']:
        data[info['name']] = info['value']

    return response

def extract(companies: list) -> list:
    """ Extract data from bloomberg api """

    print("Extracting data from bloomberg api...")

    data = []

    for _, company_id in companies:
        company_stat = get_stat(company_id)

        if not company_stat:
            return "[Error] No data found"

        company_stat["stock"] = company_id
        data.append(company_stat)

    return data

def transform(data: dict) -> pd.DataFrame:
    """ Transform data from bloomberg api to dataframe """

    print("Transforming data from bloomberg api to dataframe...")

    transformed_stock = []

    for item in data:
        tmp = {}

        tmp['stock'] = item['stock']
        tmp['updated_at'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for item in item['result'][0]['table']:
            name = item['name']
            value = item['value']
            value = value.replace(',', '')

            if '%' in value:
                value = value.replace('%', '')

            if name == '5Y Net Dividend Growth':
                tmp['5Y_Net_Dividend_Growth'] = value
            elif name == 'Dividend Indicated Gross Yield':
                tmp['Dividend_Indicated_Gross_Yield'] = value
            elif name == 'Market Cap (M)':
                tmp['Market_Cap_in_M'] = value
            elif name == 'Shares Outstanding (M)':
                tmp['Shares_Outstanding_in_M'] = value
            elif name == 'Average Volume (30-day)':
                tmp['Average_Volume_30_day'] = value

        transformed_stock.append(tmp)

    df = pd.DataFrame(transformed_stock)
    df.columns = map(str.lower, df.columns)

    return df

def load(df: pd.DataFrame) -> str:
    """ Load dataframe to BigQuery """

    print("Loading dataframe to BigQuery...")

    df_json_object = json.loads(df.to_json(orient='records'))

    sa_path = os.path.join(os.getcwd(), "creds", "serverless-sa.json")
    bq_client = bigquery.Client.from_service_account_json(sa_path)

    job_config = bigquery.LoadJobConfig(
        autodetect=True,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="updated_at",
        ),
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    table_id = BQ_PROJECT_DATASET_TABLE

    job = bq_client.load_table_from_json(
        df_json_object, table_id, job_config=job_config
    )

    job.result()

    if job.errors:
        return f"Encountered errors while inserting rows: {job.errors}"

    return "Success"

def pubsub_entrypoint(event, context=None):
    """Triggered from a message on a Cloud Pub/Sub topic.
    Args:
         event (dict): Event payload.
         context (google.cloud.functions.Context): Metadata for the event.
    """

    # pubsub_message = base64.b64decode(event['data']).decode('utf-8')
    pubsub_message = event['data']
    print(pubsub_message)

    if pubsub_message != "Invoke":
        print("No action requested")

        return 0

    # Extract and Transform
    data = extract(companies) 
    df = transform(data) 

    # Load to BigQuery
    rows_to_insert = len(df)
    print(rows_to_insert, " new record(s) are found")

    load_result = load(df)

    if load_result == "Success":
        print("New rows have been added.")
        print(f"{rows_to_insert} record(s) inserted to BigQuery Table")
    else:
        print(f"Encountered errors while inserting rows: {load_result}")


if __name__ == "__main__":
    event = {
        "data": "Invoke"
    }

    context = {}

    pubsub_entrypoint(event, context)