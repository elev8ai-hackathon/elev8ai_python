import base64
import json
import os
import time
from datetime import datetime

import boto3
from requests_toolbelt.multipart import decoder

# Initialize clients
bedrock_client = boto3.client('bedrock-agent', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client("s3", region_name='us-east-1')
lambda_client = boto3.client('lambda', region_name='us-east-1')
table = dynamodb.Table('Elev8-ai-summary')


def invoke_evaluator_lambda(email, name, to_designation, from_designation):
    """Invoke the evaluator Lambda function with the metadata"""
    try:
        payload = {
            "email": email,
            "name": name,
            "to_designation": to_designation,
            "from_designation": from_designation,
        }

        response = lambda_client.invoke(
            FunctionName='Elev8AI-Evaluator',
            InvocationType='Event',  # Asynchronous invocation
            Payload=json.dumps(payload)
        )

        print(f"Evaluator Lambda invoked. Response: {response}")
        return True
    except Exception as e:
        print(f"Error invoking evaluator Lambda: {str(e)}")
        return False


def update_sync_status(email, status, error_message=None):
    """Update sync status in DynamoDB without affecting other attributes"""
    timestamp = datetime.utcnow().isoformat()

    update_expression = "SET #status = :status, #last_updated = :timestamp"
    expression_attribute_names = {
        '#status': 'status',
        '#last_updated': 'last_updated'
    }
    expression_attribute_values = {
        ':status': status,
        ':timestamp': timestamp
    }

    if error_message:
        update_expression += ", #error = :error"
        expression_attribute_names['#error'] = 'error_message'
        expression_attribute_values[':error'] = error_message

    try:
        table.update_item(
            Key={'email': email},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
    except Exception as e:
        print(f"Error updating DynamoDB: {str(e)}")
        raise


def check_data_source_status(knowledge_base_id, data_source_id):
    """Check the status of the data source"""
    try:
        response = bedrock_client.get_data_source(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id
        )
        status = response['dataSource']['status']
        print(f"Data source status: {status}")
        return status
    except Exception as e:
        print(f"Error checking data source status: {str(e)}")
        return 'FAILED'


def process_multipart_data(body, content_type):
    """Process multipart form data and return form data dictionary"""
    if isinstance(body, str):
        body = body.encode('utf-8')

    decoded_body = base64.b64decode(body)
    multipart_data = decoder.MultipartDecoder(decoded_body, content_type)

    form_data = {}
    for part in multipart_data.parts:
        headers = {k.decode(): v.decode() for k, v in part.headers.items()}
        if 'Content-Disposition' in headers:
            disposition = headers['Content-Disposition']
            if 'filename' not in disposition:
                name = disposition.split('name=')[1].strip('"')
                value = part.text.strip()
                form_data[name] = value
            else:
                filename = disposition.split('filename=')[1].strip('"')
                form_data['file'] = {
                    'filename': filename,
                    'content': part.content
                }
    return form_data


def upload_to_s3(bucket, file_content, file_name, metadata_content, metadata_file_name):
    """Upload file and metadata to S3"""
    try:
        s3.put_object(
            Bucket=bucket,
            Key=file_name,
            ContentType='application/pdf',
            Body=file_content
        )

        s3.put_object(
            Bucket=bucket,
            Key=metadata_file_name,
            ContentType='application/json',
            Body=metadata_content
        )
        return True
    except Exception as e:
        print(f"Error uploading to S3: {str(e)}")
        raise


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'POST,OPTIONS',
                'Content-Type': 'application/json'
            },
            'body': json.dumps({})
        }
    try:
        # Get environment variables
        KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")
        DATA_SOURCE_ID = os.getenv("DATA_SOURCE_ID")

        if not KNOWLEDGE_BASE_ID or not DATA_SOURCE_ID:
            raise ValueError("Missing required environment variables")

        # Validate request
        body = event.get('body')
        if not body:
            raise ValueError("Request body is empty")

        content_type = event['headers'].get('Content-Type', event['headers'].get('content-type'))
        if not content_type:
            raise ValueError("Content-Type header is missing")

        # Process form data
        form_data = process_multipart_data(body, content_type)

        # Extract form fields
        file = form_data.get("file")
        email = form_data.get("email")
        name = form_data.get("name")
        to_designation = form_data.get("to_designation")
        from_designation = form_data.get("from_designation")

        if not all([file, email, name, to_designation, from_designation]):
            raise ValueError("Missing required form fields")

        # Prepare file names and metadata
        file_name = f'artifacts/{email.split("@")[0]}/{email.split("@")[0]}.pdf'
        metadata_file_name = f'{file_name}.metadata.json'

        metadata = {
            "metadataAttributes": {
                "email": email,
                "name": name,
                "to_designation": to_designation,
                "from_designation": from_designation,
                "tags": ["artifact", "arko tags"]
            }
        }

        # Upload to S3
        upload_to_s3(
            'elev8ai',
            file['content'],
            file_name,
            json.dumps(metadata),
            metadata_file_name
        )

        # Start knowledge base sync
        try:
            bedrock_client.start_ingestion_job(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                dataSourceId=DATA_SOURCE_ID
            )
        except Exception as e:
            print(f"Error starting sync job: {str(e)}")
            # Continue even if sync start fails, as we'll check status below

        # Update initial status
        update_sync_status(email, 'IN_PROGRESS')

        # Poll for completion
        max_attempts = 30
        attempt = 0
        last_status = None

        while attempt < max_attempts:
            status = check_data_source_status(KNOWLEDGE_BASE_ID, DATA_SOURCE_ID)
            last_status = status
            print(last_status)
            time.sleep(60)

            if status == 'AVAILABLE':
                # Invoke evaluator Lambda before returning success
                invoke_evaluator_lambda(email, name, to_designation, from_designation)

                update_sync_status(email, 'COMPLETED')
                return {
                    'statusCode': 200,
                    "headers": {
                        "Access-Control-Allow-Origin": "*",  # Or your specific domain
                        "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                        "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
                        "Content-Type": "application/json"
                    },
                    'body': json.dumps({
                        'message': 'File uploaded and knowledge base is available',
                        'status': status,
                        'evaluator_invoked': True
                    })
                }
            elif status in ['CREATING', 'UPDATING']:
                print(f"Knowledge base data source is being prepared (status: {status})")
                time.sleep(10)
                attempt += 1
            elif status == 'FAILED':
                error_message = 'Knowledge base data source failed to sync'
                update_sync_status(email, 'FAILED', error_message)
                return {
                    'statusCode': 500,
                    "headers": {
                        "Access-Control-Allow-Origin": "*",  # Or your specific domain
                        "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                        "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
                        "Content-Type": "application/json"
                    },
                    'body': json.dumps({
                        'message': error_message,
                        'status': status
                    })
                }
            else:
                print(f"Current status: {status}")
                time.sleep(10)
                attempt += 1

        # Timeout case
        timeout_message = f'Knowledge base did not become available after {max_attempts} attempts'
        update_sync_status(email, 'TIMEOUT', timeout_message)
        return {
            'statusCode': 408,
            'body': json.dumps({
                'message': timeout_message,
                'lastStatus': last_status
            }),
            "headers": {
                "Access-Control-Allow-Origin": "*",  # Or your specific domain
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
                "Content-Type": "application/json"
            },
        }

    except Exception as e:
        error_message = str(e)
        print(f"Error: {error_message}")

        response_body = {
            'message': 'Error processing request',
            'error': error_message
        }

        if 'email' in locals():
            response_body['email'] = email
            try:
                update_sync_status(email, 'FAILED', error_message)
            except Exception as db_error:
                print(f"Failed to update DynamoDB: {str(db_error)}")
                response_body['dbUpdateError'] = str(db_error)

        return {
            'statusCode': 500,
            'body': json.dumps(response_body),
            "headers": {
                "Access-Control-Allow-Origin": "*",  # Or your specific domain
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
                "Content-Type": "application/json"
            },
        }
