import json

import boto3


def lambda_handler(event, context):
    try:
        result = {
            "message": "Success",
            "data": "Your data here"
        }

        TABLE_NAME = 'Elev8-ai-summary'

        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(TABLE_NAME)

        response = table.scan()

        emails = [item['email'] for item in response.get('Items', [])]

        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",  # Or your specific domain
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,OPTIONS",
                "Content-Type": "application/json"
            },
            'body': json.dumps({'emails': emails}),
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
