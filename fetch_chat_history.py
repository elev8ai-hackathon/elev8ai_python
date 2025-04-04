import json

import boto3
from botocore.exceptions import ClientError


def lambda_handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('chat-history')

    email = event.get('queryStringParameters').get('email')

    try:
        response = table.get_item(
            Key={
                'email': email
            }
        )

        if 'Item' in response:
            response = json.dumps(response['Item'])

            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json'
                },
                'body': response
            }
        else:
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json'
                },
                'body': json.dumps({'message': 'No data found'})
            }

    except ClientError as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps(e.response['Error']['Message'])
        }
