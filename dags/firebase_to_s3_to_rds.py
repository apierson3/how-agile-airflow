from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.operators.s3 import S3CreateObjectOperator
from datetime import datetime
import csv
import io
import firebase_admin
from firebase_admin import credentials, firestore
import boto3
from datetime import datetime, timedelta

s3_filename = f'userProfile_{datetime.utcnow()}.csv'
bucket_name = 'howagile-dataengineering'

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 0,
}

# Initialize Firebase Admin SDK
cred = credentials.Certificate('/opt/airflow/config/how-agile-firebase-adminsdk-3lvow-a12fc47af9.json')
firebase_admin.initialize_app(cred)

def query_firestore(**kwargs):
    db = firestore.client()
    # Calculate the delta
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    # Define collection name and query
    docs = db.collection('userProfile').where('modifiedTs', '>=', two_days_ago).stream()
    
    results = []
    for doc in docs:
        results.append(doc.to_dict())
    
    return results

def parse_to_csv(**kwargs):
    ti = kwargs['ti']
    results = ti.xcom_pull(task_ids='query_firestore')
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write CSV header dynamically
    header = results[0].keys()
    writer.writerow(header)

    # Write CSV rows dynamically
    for result in results:
        writer.writerow(result.values())
    
    csv_data = output.getvalue()
    output.close()

    ti.xcom_push(key='csv_data', value=csv_data)

with DAG("firestore_to_s3",
         default_args=default_args,
         schedule_interval="@daily",
         catchup=False) as dag:

    query_firestore_task = PythonOperator(
        task_id='query_firestore',
        python_callable=query_firestore
    )

    parse_to_csv_task = PythonOperator(
        task_id='parse_to_csv',
        python_callable=parse_to_csv
    )

    upload_to_s3_task = S3CreateObjectOperator(
        task_id='upload_to_s3',
        s3_bucket=bucket_name,
        s3_key=s3_filename,
        data="{{ task_instance.xcom_pull(task_ids='parse_to_csv', key='csv_data') }}",
        replace=True,
        aws_conn_id='s3_conn'
    )

    query_firestore_task >> parse_to_csv_task >> upload_to_s3_task
