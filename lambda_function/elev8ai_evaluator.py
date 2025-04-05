import json
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

s3 = boto3.client('s3')
S3_BUCKET = "elev8ai"
MATRIX_FILE = "competency_matrix.json"

# Configure Bedrock client with longer timeouts and retries
bedrock_config = Config(
    connect_timeout=900,  # 9 minutes
    read_timeout=900  # 9 minutes
)


def get_matrix_from_s3(bucket, key):
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        matrix_data = json.loads(response['Body'].read().decode('utf-8'))
        return matrix_data
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            raise Exception(f"The competency matrix file {key} was not found in bucket {bucket}")
        else:
            raise Exception(f"Error retrieving matrix from S3: {str(e)}")
    except json.JSONDecodeError:
        raise Exception("Failed to parse competency matrix JSON file")


def lambda_handler(event, context):
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    KNOWLEDGE_BASE_ID = os.getenv("KNOWLEDGE_BASE_ID")
    MODEL_ARN = os.getenv("MODEL_ARN")

    client = boto3.client(
        "bedrock-agent-runtime",
        region_name=AWS_REGION,
        config=bedrock_config
    )

    print("event::::", json.dumps(event))

    try:
        # Extract required fields from metadata
        candidate_email = event.get('email')
        to_designation = event.get('to_designation')

        from_designation = event.get('from_designation')

        if not candidate_email:
            return error_response(400, "Email is required in metadataAttributes")

        # Get competency matrix
        matrix_json = get_matrix_from_s3(S3_BUCKET, MATRIX_FILE)
        matrix = json.dumps(matrix_json, ensure_ascii=False)  # Prevent escaping characters

        # Process with Bedrock
        response = client.retrieve_and_generate(
            input={"text": candidate_email},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                    "modelArn": MODEL_ARN,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {
                            "numberOfResults": 50,
                            "overrideSearchType": "SEMANTIC",
                            "filter": {
                                "equals": {
                                    "key": "email",
                                    "value": candidate_email
                                }
                            }
                        }
                    },
                    "generationConfiguration": {
                        "promptTemplate": {
                            "textPromptTemplate": f"""
                            Competency Matrix Context is provided in <<< >>>
                            <<<{matrix}>>>

                            Retrieved Knowledge:
                            $search_results$

                            **Role & Objective:**  
                            You are responsible for assessing an engineer's competency based on an uploaded artifact. Your task is to analyze the artifact and determine how well it aligns with predefined weighted competency themes.

                            **Evaluation Process:**  
                            You are provided with a weighted competency matrix that defines multiple themes across engineering levels (P2-P7). The candidate's artifact consists of raw text. 
                            The candidate is transitioning from {from_designation} to {to_designation}


                            Instructions:
                            1. Analyze ALL areas, attributes, and competencies from the P3 level in the matrix.
                            2. For each competency, provide:
                                - Match percentage (0-100%)
                                - Summary of evidence found (or lack thereof)
                            3. For competencies with no evidence, explicitly state "No evidence found in artifacts"
                            4. Calculate overall match percentage based on all competencies as the json key final_match


                            Expected JSON Output delimited by triple backticks
                            Your response must be a single, flat JSON object with the following exact top-level keys:

                            "summary": A detailed written summary of the candidate's work
                            "competency_matches": A flat object where each key is a competency name and its value is an object with "description", "match_percentage" (number), and "reasoning"
                            "area_matches": A flat object where each key is an area name and the value is the match percentage (number)
                            "category_matches": A flat object where each key is a category name and the value is the match percentage (number)
                            "final_match": A number representing the final weighted score
                            "areas_of_improvement": An array of objects, each with:
                            "competency": competency name
                            "match_percentage"
                            "feedback": Constructive feedback using the hamburger model (reality, feedback, future)

                            The output must follow the  example structure described below exactly. this is a mjust:

                            {{
                                "summary": "detailed summary of the candidate's work",
                                "final_match": 69,
                                "competency_matches": [
                                    {{
                                        "name": "writing_code",
                                        "description": "Make sure to elaborate a little bit on the description side",
                                        "match_percentage": 85,
                                        "reasoning": "The artifact includes references to unit testing and error handling, demonstrating alignment with this competency."
                                    }}
                                ],
                                "area_matches": [
                                    {{
                                        "name": "quality_and_testing",
                                        "match_percentage": 82
                                    }}
                                ],
                                "category_matches": [
                                    {{
                                        "name": "technical_skills",
                                        "match_percentage": 80
                                    }}
                                ....and so on and so forth other categories
                                ],
                                "final_match": 81,
                                "areas_of_improvement": [
                                    {{
                                        "competency": "competency name",
                                        "match_percentage": 70,
                                        "feedback": "Constructive feedback using the hamburger model (reality, feedback, future)"
                                    }}
                                ]
                            }}

                            **Weight Considerations:**  
                            - Technical skills weigh more heavily for junior levels (P2-P4)  
                            - Leadership/Strategic impact dominate senior levels (P6-P7)  
                            - Delivery/Communication maintain medium weight throughout  
                            - Normalize all weights relative to level expectations  

                            **Guardrails:**  
                            - Include all areas/attributes/competencies even if no evidence exists
                            - Be strict in assessment - no evidence means 0% match
                            - Strictly use only provided matrix and artifact   
                            - Weights must factor into all calculations  
                            - JSON output only - no explanatory text or markdown  
                            - In the json only keep the actual json content and be sure to REMOVE any delimiters like ``` or any backslash or any backslash n in the response please.

                            **Output Requirements:**  
                            - Only provide clean JSON without any formatting or delimiters
                            - No additional commentary  
                            - Maintain strict adherence to the format  
                            - Triple backticks are forbidden in output
                            - Only provide the clean JSON object. Do not escape any characters. Output only valid JSON, not a stringified version.

                            In addition to the summary key in the JSON, include an 'areas_of_improvement' section. For each competency where the match is below 100%, analyze the gap and identify where the candidate fell short. Provide clear, constructive suggestions on how the candidate can improve in those specific areas to close the remaining percentage gap based on the competency matrix. I want you to generate the constructive feedback based on the hamburger feedback methodology. Start with the reality(the open), followed by the feedback (the meaty bit) and the future(close).
                            """
                        }
                    }
                },
                "type": "KNOWLEDGE_BASE"
            }
        )
        print("response from llm", response)

        # Get the raw text from the response
        raw_response = response["output"]["text"]

        # Clean up the response
        raw_response = raw_response.strip()

        # Try to parse the JSON response
        try:
            assessment_result = json.loads(raw_response)
            print("Successfully parsed JSON response")
        except json.JSONDecodeError as e:
            print(f"JSON parsing error: {str(e)}")
            print(f"Raw response: {raw_response}")
            # If we can't parse it, return it as is
            assessment_result = raw_response

        # Store in DynamoDB
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table('Elev8-ai-summary')

        # Store the full metadata along with the summary
        response = table.update_item(
            Key={
                'email': candidate_email
            },
            UpdateExpression="SET summary_json = :s",
            ExpressionAttributeValues={
                ':s': assessment_result if isinstance(assessment_result, str) else json.dumps(assessment_result)
            },
            ReturnValues="UPDATED_NEW"
        )

        return success_response(assessment_result)

    except Exception as e:
        import traceback
        print(f"Error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return error_response(500, str(e))


def success_response(data):
    return {
        "statusCode": 200,
        "body": json.dumps({"response": data}),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        }
    }


def error_response(code, message):
    return {
        "statusCode": code,
        "body": json.dumps({"error": message}),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        }
    }
