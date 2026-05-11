#!/bin/bash

# 服务器部署脚本 - tidandemo
# 使用方法: ./deploy.sh

set -e

echo "================================"
echo "开始部署 tidandemo 应用"
echo "================================"

# 配置变量
IMAGE_NAME="ghcr.io/hieedaniel/tidandemo"
CONTAINER_NAME="tidandemo"

# 拉取最新镜像
echo ">>> 拉取最新 Docker 镜像..."
docker pull ${IMAGE_NAME}:latest

# 停止并删除旧容器（如果存在）
echo ">>> 停止旧容器..."
docker stop ${CONTAINER_NAME} 2>/dev/null || true
docker rm ${CONTAINER_NAME} 2>/dev/null || true

# 启动新容器
echo ">>> 启动新容器..."
docker run -d \
  --name ${CONTAINER_NAME} \
  --restart unless-stopped \
  -p 8501:8501 \
  -e ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN}" \
  -e ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL}" \
  -e ANTHROPIC_MODEL="${ANTHROPIC_MODEL}" \
  -v $(pwd)/data:/app/data \
  ${IMAGE_NAME}:latest

# 检查容器状态
echo ">>> 检查容器状态..."
sleep 5
docker ps | grep ${CONTAINER_NAME}

echo "================================"
echo "部署完成！"
echo "访问地址: http://localhost:8501"
echo "================================"