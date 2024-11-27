from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.operators.s3 import S3CreateObjectOperator, S3DeleteObjectsOperator
from airflow.providers.mysql.transfers.s3_to_mysql import S3ToMySqlOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from datetime import datetime
import json
import time
import requests
import csv
import io
from google.auth import crypt, jwt
import boto3

s3_filename = 'DMMlkp.csv'
bucket_name = 'howagile-dataengineering'
local_file_path = f'/tmp/{s3_filename}'

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 0,
}

# Constants for Google OAuth
SERVICE_ACCOUNT_KEY_PATH = '/opt/airflow/config/howagile-442817-f98146480388.json'
GOOGLE_OAUTH_URL = 'https://www.googleapis.com/oauth2/v4/token'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
]
EXPIRES_MARGIN = 300  # seconds before expiration

# Load the service account key JSON
with open(SERVICE_ACCOUNT_KEY_PATH, 'r') as key_file:
    service_account_info = json.load(key_file)

def generate_jwt_token(service_account_info):
    iat = time.time()
    exp = iat + 3600

    payload = {
        'iss': service_account_info['client_email'],
        'scope': ' '.join(SCOPES),
        'aud': GOOGLE_OAUTH_URL,
        'iat': iat,
        'exp': exp,
    }

    signer = crypt.RSASigner.from_service_account_info(service_account_info)
    jwt_token = jwt.encode(signer, payload)

    return jwt_token, exp

def get_access_token(service_account_info):
    now = time.time()
    token_expires_at = None

    # Generate JWT token
    jwt_token, exp = generate_jwt_token(service_account_info)

    # Request access token
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    payload = {
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': jwt_token,
    }
    response = requests.post(GOOGLE_OAUTH_URL, headers=headers, data=payload)
    response_data = response.json()

    access_token = response_data.get('access_token')
    token_expires_at = exp

    return access_token

def fetch_and_transform_google_sheet_data(**kwargs):
    # Get the access token
    access_token = get_access_token(service_account_info)
    
    # Use the access token to fetch data from Google Sheets
    url = "https://sheets.googleapis.com/v4/spreadsheets/1-ESRUZvSKfI3F5d0f0IqMc78KNOw8YGL3PWR_NuJd-k/values/Sheet1?alt=json"
    headers = {
        'Authorization': f'Bearer {access_token}'
    }

    response = requests.get(url, headers=headers)
    data = response.json()
    values = data.get('values', [])

    # Convert values to CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(values)
    csv_data = output.getvalue()
    output.close()

    # Return CSV data as string
    return csv_data

def read_s3_file_into_string(**kwargs):
    from airflow.hooks.base import BaseHook
    import csv
    connection = BaseHook.get_connection('s3_conn')

    aws_access_key_id = connection.login
    aws_secret_access_key = connection.password

    s3_client = boto3.client(
        's3',
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key
    )

    obj = s3_client.get_object(Bucket=bucket_name, Key=s3_filename)
    csv_data = obj['Body'].read().decode('utf-8')

    reader = csv.reader(io.StringIO(csv_data))
    next(reader)  # Skip header row
    insert_statements = []
    for row in reader:
        insert_statements.append(f"INSERT INTO stage.dmm_lookup (Score, Stage, Data, Enterprise, Leadership, Targets, Technology, Analytics) VALUES ({', '.join(map(lambda x: f'\"{x}\"', row))});")

    sql_commands = " ".join(insert_statements)
    
    ti = kwargs['ti']
    ti.xcom_push(key='sql_commands', value=sql_commands)

with DAG("gsheet_to_s3_to_rds", 
         default_args=default_args, 
         schedule_interval="@daily", 
         catchup=False) as dag:

    fetch_data = PythonOperator(
        task_id='fetch_google_sheet_data',
        python_callable=fetch_and_transform_google_sheet_data
    )

    upload_to_s3 = S3CreateObjectOperator(
        task_id='upload_to_s3',
        s3_bucket='howagile-dataengineering',
        s3_key=s3_filename,
        data="{{ task_instance.xcom_pull(task_ids='fetch_google_sheet_data') }}",
        replace=True,
        aws_conn_id='s3_conn'
    )

    truncate_mysql = SQLExecuteQueryOperator(
        task_id='truncate_mysql',
        sql=r"""TRUNCATE TABLE stage.dmm_lookup;""",
        conn_id='mysql_conn'
    )

    read_s3_file = PythonOperator(
        task_id='read_s3_file_into_string',
        python_callable=read_s3_file_into_string
    )

    load_to_mysql = SQLExecuteQueryOperator(
        task_id='load_to_mysql',
        sql="{{ task_instance.xcom_pull(task_ids='read_s3_file_into_string', key='sql_commands') }}",
        conn_id='mysql_conn'
    )

    delete_s3_file = S3DeleteObjectsOperator( 
        task_id='delete_s3_file', 
        bucket=bucket_name, 
        keys=s3_filename, 
        aws_conn_id='s3_conn' 
    )

    fetch_data >> upload_to_s3 >> truncate_mysql >> read_s3_file >> load_to_mysql >> delete_s3_file
