import json
import boto3
import base64
from botocore.exceptions import ClientError

def lambda_handler(event, context):
   dynamodb = boto3.resource('dynamodb')
   table = dynamodb.Table('chat-history')
   email = event.get('queryStringParameters').get('email')
   request_body = base64.b64decode(event.get('body')).decode('utf-8')

   try:
    response = table.get_item(
        Key = {
            'email': email
        }
    )
    chat = json.loads(request_body)

    if 'Item' in response:
        put_res = table.update_item(
            Key = {
                'email': email
            },
            UpdateExpression = 'SET chatHistory = list_append(chatHistory, :chatHistory)',
            ExpressionAttributeValues = {
                ':chatHistory': [chat]
            },
            ReturnValues = 'UPDATED_NEW'
        )

        return {
            'statusCode': 200,
            'headers': {
            'Content-Type': 'application/json'
            },
            'body': json.dumps(put_res['Attributes']['chatHistory'][-1])
        }
    else:
        table.put_item(
            Item = {
                'email': email,
                'chatHistory': [chat]
            }
        )
        return {
            'statusCode': 200,
            'headers': {
            'Content-Type': 'application/json'
            },
            'body': json.dumps(chat)
        }
   except ClientError as e:
    return {
      'statusCode': 500,
      'headers': {
            'Content-Type': 'application/json'
       },
      'body': json.dumps(e.response['Error']['Message'])
    }

