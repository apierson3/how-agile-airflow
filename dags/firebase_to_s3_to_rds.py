from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.operators.s3 import S3CreateObjectOperator, S3DeleteObjectsOperator
from datetime import datetime, timedelta
import csv
import io
import requests
import json
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
import boto3

s3_filename = f'userProfile_{datetime.utcnow().strftime("%Y-%m-%d")}.csv'
bucket_name = 'howagile-dataengineering'

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 0,
}

# Constants for Google OAuth
SERVICE_ACCOUNT_KEY_PATH = '/opt/airflow/config/how-agile-48610d590f06.json'
GOOGLE_OAUTH_URL = 'https://www.googleapis.com/oauth2/v4/token'
SCOPES = [
    'https://www.googleapis.com/auth/datastore',
]

def get_access_token():
    credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY_PATH, scopes=SCOPES)
    credentials.refresh(Request())
    return credentials.token

def fetch_firestore_data(**kwargs):
    access_token = get_access_token()
    url = 'https://firestore.googleapis.com/v1/projects/how-agile/databases/(default)/documents/userProfile'
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    
    response = requests.get(url, headers=headers)
    data = response.json().get('documents', [])

    results = [doc['fields'] for doc in data]
    kwargs['ti'].xcom_push(key='firestore_data', value=results)

def json_to_csv(data):
    output = io.StringIO()
    writer = csv.writer(output)
    
    if data:
        header = data[0].keys()
        writer.writerow(header)
        
        for item in data:
            row = []
            for key in header:
                value = item[key]
                if 'stringValue' in value:
                    row.append(value['stringValue'])
                elif 'integerValue' in value:
                    row.append(value['integerValue'])
                elif 'doubleValue' in value:
                    row.append(value['doubleValue'])
                elif 'booleanValue' in value:
                    row.append(value['booleanValue'])
                elif 'timestampValue' in value:
                    row.append(value['timestampValue'])
                elif 'arrayValue' in value:
                    row.append(json.dumps(value['arrayValue']['values']))
                elif 'mapValue' in value:
                    row.append(json.dumps(value['mapValue']['fields']))
                else:
                    row.append('')
            writer.writerow(row)
    
    csv_data = output.getvalue()
    output.close()
    return csv_data

def transform_and_upload_to_s3(**kwargs):
    data = kwargs['ti'].xcom_pull(key='firestore_data', task_ids='fetch_firestore_data')
    csv_data = json_to_csv(data)
    kwargs['ti'].xcom_push(key='csv_data', value=csv_data)

def read_s3_file_into_string(**kwargs):
    from airflow.hooks.base import BaseHook
    import csv
    import io
    import boto3

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
    headers = next(reader)  # Read the header row

    # Ensure the header is in the expected order
    expected_order = ['companyName', 'companyIndustry', 'createdTs', 'uid', 'firstName', 'lastName', 'modifiedTs']
    
    insert_statements = []
    for row in reader:
        # Use a dictionary to map the CSV values to their headers
        row_dict = dict(zip(headers, row))
        
        # Create the insert statement using the expected order
        insert_statements.append(f"""
            INSERT INTO stage.user_profile (
                companyName, companyIndustry, createdTs, uid, firstName, lastName, modifiedTs
            ) VALUES (
                "{row_dict['companyName']}", "{row_dict['companyIndustry']}", "{row_dict['createdTs']}", "{row_dict['uid']}", "{row_dict['firstName']}", "{row_dict['lastName']}", "{row_dict['modifiedTs']}"
            );
        """)

    sql_commands = " ".join(insert_statements)
    
    ti = kwargs['ti']
    ti.xcom_push(key='sql_commands', value=sql_commands)
    print(csv_data)
    print(sql_commands)

with DAG("firestore_to_s3_to_rds",
         default_args=default_args,
         schedule_interval="@daily",
         catchup=False) as dag:

    fetch_firestore_data_task = PythonOperator(
        task_id='fetch_firestore_data',
        python_callable=fetch_firestore_data
    )

    transform_and_upload_to_s3_task = PythonOperator(
        task_id='transform_and_upload_to_s3',
        python_callable=transform_and_upload_to_s3
    )

    upload_to_s3_task = S3CreateObjectOperator(
        task_id='upload_to_s3',
        s3_bucket=bucket_name,
        s3_key=s3_filename,
        data="{{ task_instance.xcom_pull(task_ids='transform_and_upload_to_s3', key='csv_data') }}",
        replace=True,
        aws_conn_id='s3_conn'
    )

    truncate_mysql = SQLExecuteQueryOperator(
        task_id='truncate_mysql',
        sql=r"""TRUNCATE TABLE stage.user_profile;""",
        conn_id='mysql_conn'
    )

    read_s3_file = PythonOperator(
        task_id='read_s3_file_into_string',
        python_callable=read_s3_file_into_string
    )

    stage_user_profile = SQLExecuteQueryOperator(
        task_id='stage_user_profile',
        sql="{{ task_instance.xcom_pull(task_ids='read_s3_file_into_string', key='sql_commands') }}",
        conn_id='mysql_conn'
    )

    exec_fact_load = SQLExecuteQueryOperator(
        task_id='exec_fact_load',
        sql=r"""CALL dbo.update_user_fact;""",
        conn_id='mysql_conn'
    )
    
    delete_s3_file = S3DeleteObjectsOperator( 
        task_id='delete_s3_file', 
        bucket=bucket_name, 
        keys=s3_filename, 
        aws_conn_id='s3_conn' 
    )


    fetch_firestore_data_task >> transform_and_upload_to_s3_task >> upload_to_s3_task >> truncate_mysql >> read_s3_file >> stage_user_profile >> exec_fact_load >> delete_s3_file
