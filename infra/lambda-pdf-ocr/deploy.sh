#!/bin/bash
# AHNJ PDF OCR Lambda — one-shot deploy script.
#
# Idempotent: ECR repo, IAM role, Lambda function are all created on first
# run, updated on subsequent runs.
#
# Usage:
#   cd infra/lambda-pdf-ocr
#   ./deploy.sh
#
# Requires: aws CLI, docker, configured AWS creds for the AHNJ account.

set -e

REGION=${AWS_REGION:-us-east-1}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=ah-pdf-ocr
FN=ah-pdf-ocr
TAG=$(date +%Y%m%d-%H%M%S)
IMAGE_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${TAG}"

echo "=== AHNJ Lambda OCR deploy ==="
echo "  account:  $ACCOUNT"
echo "  region:   $REGION"
echo "  image:    $IMAGE_URI"
echo ""

# 1. ECR repository (create if missing)
echo "[1/6] ensuring ECR repository '${REPO}' exists…"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$REPO" --region "$REGION" --image-scanning-configuration scanOnPush=true >/dev/null

# 2. Login + build + push
echo "[2/6] docker login to ECR…"
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "[3/6] docker build (linux/amd64, single-arch manifest for Lambda)…"
# Lambda rejects buildx OCI manifest lists ("image manifest type not supported").
# --provenance=false disables the buildx provenance attestation which would
# otherwise produce a multi-platform manifest. We also use --output=type=docker
# so the result is a plain Docker v2 image (not an OCI artifact).
docker buildx build \
    --platform linux/amd64 \
    --provenance=false \
    --output=type=docker \
    -t "${REPO}:${TAG}" .
docker tag "${REPO}:${TAG}" "${IMAGE_URI}"

echo "[4/6] docker push…"
docker push "${IMAGE_URI}"

# 3. IAM role
ROLE_NAME=ah-pdf-ocr-role
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

echo "[5/6] ensuring IAM role '${ROLE_NAME}' exists…"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST_POLICY" >/dev/null
    # Lambda basic execution (CloudWatch logs)
    aws iam attach-role-policy --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    # S3 read access on the AHNJ bucket
    aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name s3-read \
        --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject"],"Resource":"arn:aws:s3:::atlantic-highlands-documents-*/*"}]}'
    echo "  created role; sleeping 8s for IAM propagation…"
    sleep 8
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# 4. Lambda function
echo "[6/6] create-or-update lambda '${FN}'…"
if aws lambda get-function --function-name "$FN" --region "$REGION" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FN" --image-uri "$IMAGE_URI" --region "$REGION" >/dev/null
    aws lambda wait function-updated --function-name "$FN" --region "$REGION"
    aws lambda update-function-configuration --function-name "$FN" \
        --memory-size 2048 --timeout 300 \
        --environment "Variables={S3_BUCKET=atlantic-highlands-documents-${ACCOUNT}}" \
        --region "$REGION" >/dev/null
else
    aws lambda create-function --function-name "$FN" \
        --package-type Image \
        --code "ImageUri=${IMAGE_URI}" \
        --role "$ROLE_ARN" \
        --memory-size 2048 \
        --timeout 300 \
        --environment "Variables={S3_BUCKET=atlantic-highlands-documents-${ACCOUNT}}" \
        --region "$REGION" >/dev/null
    aws lambda wait function-active-v2 --function-name "$FN" --region "$REGION"
fi

# Reserved concurrency: cap to friendly burst that won't stress RDS via the
# EC2 driver writing back. Bump if needed.
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 50 --region "$REGION" >/dev/null

echo ""
echo "DONE."
echo "  function:    $FN"
echo "  image:       $IMAGE_URI"
echo "  concurrency: 50"
echo ""
echo "Test:"
echo "  aws lambda invoke --function-name $FN --payload '{\"pdf_key\":\"<some.pdf>\"}' /tmp/out.json --cli-binary-format raw-in-base64-out"
echo "  cat /tmp/out.json | jq .pages_ocrd"
