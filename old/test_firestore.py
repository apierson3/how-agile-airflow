from airflow import DAG
from airflow.providers.google.cloud.hooks.firestore import FirestoreHook
from airflow.operators.python import PythonOperator
from datetime import datetime

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 11, 25),
    'retries': 1,
}

def verify_firestore_connection(**kwargs):
    firestore_hook = FirestoreHook(gcp_conn_id='google_cloud_default')
    firestore_hook.list_collection_ids(collection_id='your-collection-id')

with DAG("verify_firestore_connection", 
         default_args=default_args, 
         schedule_interval="@daily", 
         catchup=False) as dag:

    task_verify_firestore = PythonOperator(
        task_id='verify_firestore_connection',
        python_callable=verify_firestore_connection
    )

    task_verify_firestore
