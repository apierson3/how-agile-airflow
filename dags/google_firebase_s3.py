from airflow import DAG
from airflow.providers.amazon.aws.transfers.google_api_to_s3 import GoogleApiToS3Operator
from airflow.providers.amazon.aws.operators.s3 import S3CreateBucketOperator
from airflow.providers.amazon.aws.operators.s3 import S3DeleteBucketOperator
from airflow.utils.dates import days_ago
from airflow.utils.trigger_rule import TriggerRule
from datetime import datetime
from airflow.operators.dummy import DummyOperator


default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 1,
}

with DAG("google_firebase_s3", start_date=datetime(2024, 11, 25),
         schedule_interval="@daily", catchup=False, default_args=default_args) as dag:
    
    task_google_sheets_values_to_s3 = GoogleApiToS3Operator(
        task_id='google_sheet_data_to_s3',
        google_api_service_name='sheets',
        google_api_service_version='v4',
        google_api_endpoint_path='sheets.spreadsheets.values.get',
        google_api_endpoint_params={
            'spreadsheetId': '1-ESRUZvSKfI3F5d0f0IqMc78KNOw8YGL3PWR_NuJd-k',
            'range': 'Sheet1'
        },
        s3_destination_key='s3://howagile-dataengineering/DMMLookup.csv'
    )

    start = DummyOperator(task_id='Starting', dag=dag)

    start >> copy_sheet_to_gcs
