from airflow import DAG
from airflow.providers.google.cloud.hooks.firestore import FirestoreHook
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.operators.python import PythonOperator
from datetime import datetime

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 0,
}

def load_firestore_to_s3(**kwargs):
    # Initialize Firestore and S3 hooks
    firestore_hook = FirestoreHook()
    s3_hook = S3Hook(aws_conn_id='s3_conn')

    # Read data from Firestore
    collection_name = 'userProfile'
    documents = firestore_hook.list_documents(collection=collection_name)

    # Prepare data for upload
    data = []
    for doc in documents:
        data.append(doc.to_dict())

    # Convert data to string and upload to S3
    file_name = 'test.json'
    s3_hook.load_string(
        string_data=str(data),
        key=file_name,
        bucket_name='howagile-dataengineering',
        replace=True
    )

with DAG("firestore_to_s3", 
         default_args=default_args, 
         schedule_interval="@daily", 
         catchup=False) as dag:

    task_load_firestore_to_s3 = PythonOperator(
        task_id='load_firestore_to_s3',
        python_callable=load_firestore_to_s3
    )

    start = DummyOperator(task_id='Starting', dag=dag)

    start >> task_load_firestore_to_s3
