# Implementation Plan: Object Detection Platform

## Overview

本实施计划将 CVAT 改造为端到端目标检测平台。实施顺序遵循"基础设施  数据层  业务逻辑  API  前端  集成"的原则，确保每个阶段都可独立验证。平台新增 5 个 Django App（training、model_registry、inference、evaluation、dataset_manager 扩展），通过 RQ 异步队列执行长时任务，复用 CVAT 现有权限体系和存储机制。

## Tasks

- [ ] 1. 基础设施和项目结构搭建
  - 在 `cvat/apps/` 下创建 4 个新 Django App 目录结构：`training`、`model_registry`、`inference`、`evaluation`，每个 App 包含 `__init__.py`、`apps.py`、`models.py`、`serializers.py`、`views.py`、`urls.py`、`permissions.py`、`tests/` 目录
  - 在 `cvat/settings/base.py` 的 `INSTALLED_APPS` 中注册新 App
  - 在 `cvat/settings/base.py` 的 `RQ_QUEUES` 中新增 `training`（timeout 86400s）和 `inference`（timeout 3600s）队列配置
  - 在 `requirements/` 下新增训练依赖：`ultralytics>=8.3`、`torchvision>=0.18`、`onnx>=1.16`、`onnxruntime-gpu>=1.18`、`tensorboard>=2.17`、`pycocotools>=2.0`
  - 在前端 `cvat-ui/package.json` 中新增 `recharts>=2.12` 和 `@ant-design/plots>=2.2`
  - 创建 `cvat/apps/training/rq_tasks.py`、`cvat/apps/inference/rq_tasks.py`、`cvat/apps/evaluation/rq_tasks.py`、`cvat/apps/model_registry/rq_tasks.py` 作为 RQ 工作函数入口文件
  - _Requirements: 14.1_

