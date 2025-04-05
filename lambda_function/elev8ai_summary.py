import json

import boto3


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                'Content-Type': 'application/json'
            },
            'body': json.dumps({})
        }

    try:
        TABLE_NAME = 'Elev8-ai-summary'

        print('event::::::::::', event)
        query_params = event.get('queryStringParameters', {})

        email = query_params.get('email', None)
        print('email:::::::::', email)
        if email is None:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Internal Server Error'}),
                "headers": {
                    "Access-Control-Allow-Origin": "*",  # Or your specific domain
                    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                    "Access-Control-Allow-Methods": "GET,OPTIONS",
                    "Content-Type": "application/json"
                },
            }

        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(TABLE_NAME)
        key = {'email': email}

        response = table.get_item(Key=key)
        print('response:::::::::', response)
        print('response[item]:::::::::', response['Item']['summary_json'])

        if 'Item' in response:
            return {
                "statusCode": 200,
                "headers": {
                    "Access-Control-Allow-Origin": "*",  # Or your specific domain
                    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                    "Access-Control-Allow-Methods": "GET,OPTIONS",
                    "Content-Type": "application/json"
                },
                'body': json.dumps(response['Item']['summary_json'])
            }

        else:
            return {
                "statusCode": 404,
                "headers": {
                    "Access-Control-Allow-Origin": "*",  # Or your specific domain
                    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                    "Access-Control-Allow-Methods": "GET,OPTIONS",
                    "Content-Type": "application/json"
                },
                'body': json.dumps({'message': 'Item not found'})
            }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",  # Or your specific domain
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,OPTIONS",
                "Content-Type": "application/json"
            },
            "body": json.dumps({
                "error": str(e)
            })
        }
