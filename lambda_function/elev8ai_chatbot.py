import base64
import json
import os
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Initialize clients with proper configuration
s3 = boto3.client('s3', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')

S3_BUCKET = "elev8ai"
MATRIX_FILE = "competency_matrix.json"
DYNAMODB_TABLE = "Elev8-ai-summary"

bedrock_config = Config(
    connect_timeout=900,
    read_timeout=900
)


def get_matrix_from_s3(bucket, key):
    try:
        print(f"Attempting to fetch matrix from S3: {bucket}/{key}")
        response = s3.get_object(Bucket=bucket, Key=key)
        matrix_data = json.loads(response['Body'].read().decode('utf-8'))
        print("Successfully retrieved matrix from S3")
        return matrix_data
    except ClientError as e:
        error_msg = f"S3 ClientError retrieving matrix: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error in matrix file: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error getting matrix: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)


def get_chat_history(user_email, limit=5):
    try:
        print(f"Fetching chat history for: {user_email}")
        table = dynamodb.Table(DYNAMODB_TABLE)
        response = table.query(
            KeyConditionExpression='email = :email',
            ExpressionAttributeValues={':email': user_email},
            Limit=limit,
            ScanIndexForward=False
        )
        items = response.get('Items', [])
        print(f"Found {len(items)} chat history items")
        return items
    except ClientError as e:
        error_msg = f"DynamoDB ClientError getting chat history: {str(e)}"
        print(error_msg)
        return []
    except Exception as e:
        error_msg = f"Unexpected error getting chat history: {str(e)}"
        print(error_msg)
        return []


def store_chat_interaction(user_email, question, answer, context=None):
    try:
        print(f"Storing chat interaction for: {user_email}")
        table = dynamodb.Table(DYNAMODB_TABLE)
        timestamp = int(datetime.now().timestamp() * 1000)

        response = table.update_item(
            Key={'email': user_email},
            UpdateExpression="SET #q = :question, #a = :answer, #ts = :timestamp, #ctx = :context",
            ExpressionAttributeNames={
                '#q': 'question',
                '#a': 'answer',
                '#ts': 'timestamp',
                '#ctx': 'context'
            },
            ExpressionAttributeValues={
                ':question': question,
                ':answer': answer,
                ':timestamp': timestamp,
                ':context': context or {}
            },
            ReturnValues="UPDATED_NEW"
        )
        print("Successfully stored chat interaction")
        return response
    except ClientError as e:
        error_msg = f"DynamoDB ClientError storing chat: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error storing chat: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)


def build_chat_context(user_email, current_question):
    try:
        print(f"Building chat context for: {user_email}")
        chat_history = get_chat_history(user_email)
        if not chat_history:
            print("No chat history found")
            return f"Current question: {current_question}"

        context_lines = ["Previous conversation history:"]
        for item in reversed(chat_history):
            if 'question' in item and 'answer' in item:
                context_lines.append(f"Q: {item['question']}")
                context_lines.append(f"A: {item['answer']}")

        context_lines.append(f"Current question: {current_question}")
        context = "\n".join(context_lines)
        print(f"Built context with {len(chat_history)} history items")
        return context
    except Exception as e:
        error_msg = f"Error building chat context: {str(e)}"
        print(error_msg)
        return f"Current question: {current_question}"


def generate_chat_response(client, prompt, context, matrix, knowledge_base_id, model_arn, user_email):
    try:
        print(f"Generating response for: {user_email}")

        # Truncate the matrix content if needed (keep last part which is often most relevant)
        max_matrix_length = 5000  # Leave room for other components
        if len(matrix) > max_matrix_length:
            print(f"Truncating matrix from {len(matrix)} to {max_matrix_length} characters")
            matrix = matrix[-max_matrix_length:]  # Keep the end which often has detailed competencies

        # Truncate chat context if needed
        max_context_length = 5000
        if len(context) > max_context_length:
            print(f"Truncating context from {len(context)} to {max_context_length} characters")
            context = context[-max_context_length:]

        # Build the prompt in parts to ensure we don't exceed limits
        prompt_parts = [
            f"User Email: {user_email}",
            "Chat Context (recent conversation history):",
            context[:max_context_length],
            "\nCompetency Matrix Context (most relevant parts):",
            matrix[:max_matrix_length],
            "\nCurrent Question:",
            prompt,
            "\nInstructions:",
            """- You are an AI assistant for Elev8's competency assessment system
- For assessment questions, focus on the competency matrix
- If asked about weekness focus on areas of improvement the can focus on.
- For general questions, use your knowledge base
- Keep answers concise
- If unsure, say you don't know"""
        ]

        # Join with newlines but ensure total length < 20,000
        full_prompt = "\n".join(prompt_parts)
        if len(full_prompt) > 20000:
            print(f"Prompt too long ({len(full_prompt)}), truncating further")
            full_prompt = full_prompt[:19000] + "\n[CONTENT TRUNCATED]"

        print(f"Sending prompt of length {len(full_prompt)} to Bedrock")

        response = client.retrieve_and_generate(
            input={"text": full_prompt},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": knowledge_base_id,
                    "modelArn": model_arn,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {
                            "numberOfResults": 50,  # Reduced from 100 to limit response size
                            "overrideSearchType": "HYBRID"
                        }
                    }
                },
                "type": "KNOWLEDGE_BASE"
            }
        )

        output = response["output"]["text"].strip()
        print("Successfully generated response")
        return output

    except ClientError as e:
        error_msg = f"Bedrock ClientError generating response: {str(e)}"
        print(error_msg)
        if "ValidationException" in str(e):
            print(f"Prompt length was: {len(full_prompt)}")
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error generating response: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)


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
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")
    MODEL_ARN = os.getenv("MODEL_ARN")

    client = boto3.client(
        "bedrock-agent-runtime",
        region_name=AWS_REGION,
        config=bedrock_config
    )

    print("Received event:", json.dumps(event))

    try:
        # Extract input parameters with better error handling
        if 'requestContext' in event:  # API Gateway request
            undecoded_body = event.get('body', '{}')
            request_body = json.loads(base64.b64decode(undecoded_body).decode('utf-8'))

            print(f"Decoded body: {request_body}")

            candidate_email = request_body.get('email')
            user_input = request_body.get('input')
        else:  # Direct Lambda invocation
            candidate_email = event.get('email')
            user_input = event.get('input')

        if not candidate_email:
            raise ValueError("Missing required parameter: email")
        if not user_input:
            raise ValueError("Missing required parameter: input")

        print(f"Processing request for {candidate_email}: {user_input}")

        # Get competency matrix
        matrix_json = get_matrix_from_s3(S3_BUCKET, MATRIX_FILE)
        matrix = json.dumps(matrix_json, ensure_ascii=False)

        # Build chat context
        chat_context = build_chat_context(candidate_email, user_input)

        # Generate response
        response_text = generate_chat_response(
            client=client,
            prompt=user_input,
            context=chat_context,
            matrix=matrix,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            model_arn=MODEL_ARN,
            user_email=candidate_email  # Passing the email to the function
        )

        # Store the interaction
        store_chat_interaction(
            user_email=candidate_email,
            question=user_input,
            answer=response_text,
            context={"generated_at": datetime.now().isoformat()}
        )

        # Return the response
        return success_response({
            "answer": response_text,
            "context": chat_context
        })

    # except ValueError as e:
    #     error_msg = f"Input validation error: {str(e)}"
    #     print(error_msg)
    #     return error_response(400, error_msg)
    except Exception as e:
        error_msg = f"Processing error: {str(e)}"
        print(error_msg)
        return error_response(500, error_msg)


def success_response(data):
    return {
        "statusCode": 200,
        "body": json.dumps({"response": data}),
        "headers": {
            "Access-Control-Allow-Origin": "*",  # Or your specific domain
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
            "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
            "Content-Type": "application/json"
        },
    }


def error_response(code, message):
    return {
        "statusCode": code,
        "body": json.dumps({"error": "message"}),
        "headers": {
            "Access-Control-Allow-Origin": "*",  # Or your specific domain
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
            "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
            "Content-Type": "application/json"
        },
    }
