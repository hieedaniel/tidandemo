# 智能产品检索系统

UNV/宇视科技产品智能匹配系统 - 基于 LLM + 规则引擎的产品推荐应用

## 项目简介

销售人员输入客户需求，系统自动识别产品类别，通过 LLM 提取结构化参数，使用可配置规则引擎筛选和评分产品，最终输出推荐结果。

**核心功能：**
- 智能检索：客户需求文本 → LLM 参数提取 → 规则引擎评分
- 产品库管理：按类别导入/导出产品数据（CSV）
- 规则配置：可视化配置各类别评分规则和权重

**支持产品类别：**
- 工业相机、线扫相机、3D相机
- 网络摄像机
- 信息发布屏

## 本地运行

### 1. 环境准备

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 到 `.env`，填写以下配置：

```env
ANTHROPIC_AUTH_TOKEN=your-api-token
ANTHROPIC_BASE_URL=https://api.anthropic.com  # 或内部网关地址
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

### 3. 启动应用

```bash
streamlit run app.py
```

访问地址：http://localhost:8501

## Docker 部署

### 方式一：使用 Docker Compose（推荐）

```bash
# 1. 创建 .env 文件（填写环境变量）
cat > .env << EOF
ANTHROPIC_AUTH_TOKEN=your-token
ANTHROPIC_BASE_URL=https://aibedrock.uniview.com
ANTHROPIC_MODEL=glm-5
DOCKER_USERNAME=your-docker-username
EOF

# 2. 拉取并启动容器
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down
```

### 方式二：使用部署脚本

```bash
# 1. 编辑 deploy.sh，设置 Docker Hub 用户名
vim deploy.sh

# 2. 创建 .env 文件（同上）

# 3. 运行部署脚本
chmod +x deploy.sh
./deploy.sh
```

### 方式三：手动 Docker 命令

```bash
# 拉取镜像
docker pull your-username/tidandemo:latest

# 启动容器
docker run -d \
  --name tidandemo \
  --restart unless-stopped \
  -p 8501:8501 \
  -e ANTHROPIC_AUTH_TOKEN="your-token" \
  -e ANTHROPIC_BASE_URL="https://aibedrock.uniview.com" \
  -e ANTHROPIC_MODEL="glm-5" \
  -v $(pwd)/data:/app/data \
  your-username/tidandemo:latest

# 查看状态
docker ps | grep tidandemo

# 查看日志
docker logs tidandemo -f

# 停止容器
docker stop tidandemo
docker rm tidandemo
```

## GitHub 自动构建

本项目配置了 GitHub Actions 自动构建 Docker 镜像：

- **触发条件：** 推送到 `main` 分支或 Pull Request
- **镜像仓库：** Docker Hub (`your-username/tidandemo`)
- **镜像标签：** `latest` + commit SHA

### 配置 GitHub Secrets

在 GitHub 仓库设置中添加以下 Secrets：

1. `DOCKER_USERNAME` - Docker Hub 用户名
2. `DOCKER_PASSWORD` - Docker Hub 密码或 Access Token

路径：Settings → Secrets and variables → Actions → New repository secret

## 技术架构

### 核心模块 (`core/`)

- **DataManager**: SQLite 数据库管理（按类别分表）
- **LLMMapper**: 两步 LLM 提取（类别识别 + 参数提取）
- **RuleEngine**: 四层规则引擎（特殊规格 → 权重评分 → 标签奖励 → 价格排序）

### 数据文件 (`data/`)

- `products.db` - SQLite 数据库（按类别分表）
- `default_config.json` - 默认配置（版本控制）
- `config.json` - 用户保存的配置覆盖

## 系统要求

- Python 3.13+
- Docker（可选）
- 网络访问 Anthropic API 或内部网关

## 许可证

本项目仅供内部使用。

## 联系方式

作者：hieedaniel
邮箱：hiedlx@gmail.com
