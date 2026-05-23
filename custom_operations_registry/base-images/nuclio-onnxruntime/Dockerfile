# Nuclio 自定义操作基础镜像。
# 先基于本机已有的 cvat/server:dev，避免每个 function 都重新从 Docker Hub 拉基础系统镜像。
ARG CVAT_SERVER_IMAGE=cvat/server:dev
FROM ${CVAT_SERVER_IMAGE}

# Nuclio 构建阶段需要向 Python 环境安装 nuclio-sdk/msgpack 等运行依赖，
# 因此基础镜像保持 root。具体 function.yaml 里仍会显式设置 Nuclio processor 入口。
USER root

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# 默认使用清华源。网络环境不同可以在 docker build 时通过 --build-arg 覆盖。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ARG NUMPY_VERSION=1.26.4
ARG PILLOW_VERSION=10.4.0
ARG OPENCV_VERSION=4.10.0.84
ARG ONNXRUNTIME_VERSION=1.20.1

RUN python -m ensurepip --upgrade \
    && python -m pip install \
        -i ${PIP_INDEX_URL} \
        --trusted-host ${PIP_TRUSTED_HOST} \
        --upgrade pip setuptools wheel \
    && python -m pip install \
        -i ${PIP_INDEX_URL} \
        --trusted-host ${PIP_TRUSTED_HOST} \
        numpy==${NUMPY_VERSION} \
        pillow==${PILLOW_VERSION} \
        opencv-python-headless==${OPENCV_VERSION} \
        onnxruntime==${ONNXRUNTIME_VERSION}

WORKDIR /opt/nuclio

# 清掉 cvat/server:dev 继承来的 backend_entrypoint.sh，避免函数容器启动到 CVAT 后端入口。
ENTRYPOINT []
CMD []
