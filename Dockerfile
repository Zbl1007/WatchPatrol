# ==============================================================================
# 任务监控平台 Docker 镜像构建文件（针对国内服务器网络深度优化）
# ==============================================================================
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# 设置环境变量：北京时区、Python控制台无缓冲输出
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1. 替换 Debian 国内软件镜像源（使用阿里云镜像源，解决国内服务器 apt-get 超时问题）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
        sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
        sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list; \
    fi

# 2. 安装时区支持与基础网络工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    curl \
    && ln -fs /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

# 3. 配置 pip 国内镜像源（清华大学镜像站，备用阿里云），设置超时与信任主机
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip config set global.timeout 60

# 4. 先复制依赖清单利用 Docker 缓存层
COPY requirements.txt .

# 5. 安装 Python 依赖库
RUN pip install --no-cache-dir -r requirements.txt

# 6. 复制项目代码与静态模板
COPY . .

# 7. 确保数据持久化目录存在
RUN mkdir -p /app/data

# 默认暴露服务端口
EXPOSE 8080

# 健康检查：每 30 秒探测一次服务存活
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/login || exit 1

# 启动 Web 服务与任务调度中心
CMD ["python3", "main.py"]
