# 推理任务功能实施指南

## 一、整体思路

**不新建独立 App，在现有 `engine` App 上最小化扩展。**

- 新增 `InferenceTask` 数据库模型，关联到现有 `Task`
- 复用 `lambda_manager` 的 `LambdaGateway` + `LambdaFunction.invoke()` 调用 Nuclio 函数
- 复用现有 `annotation` RQ 队列和 Worker
- 新增一个独立的前端页面

```
用户选择 Task + 模型 + 阈值
  ↓
POST /api/inference/tasks
  ↓
写入 InferenceTask 记录 → 入队 RQ
  ↓
cvat_worker_annotation 拾取任务
  ↓
LambdaGateway.get(function_id)   ← 复用现有机制
LambdaFunction.invoke(task, ...)  ← 复用现有机制，自动写回标注
  ↓
更新 status = completed
  ↓
前端轮询显示结果
```

---

## 二、需要改动的文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `cvat/apps/engine/models.py` | **修改** | 末尾添加 `InferenceTaskStatus` 枚举 + `InferenceTask` 模型 |
| `cvat/apps/engine/serializers.py` | **修改** | 末尾添加两个序列化器 |
| `cvat/apps/engine/inference.py` | **新建** | RQ 后台任务函数 |
| `cvat/apps/engine/views.py` | **修改** | 末尾添加 `InferenceTaskViewSet` |
| `cvat/apps/engine/urls.py` | **修改** | 注册新路由 |
| `cvat/apps/engine/rules/inference_tasks.rego` | **新建** | OPA 权限规则 |
| `cvat/apps/engine/migrations/XXXX_add_inference_task.py` | **自动生成** | 运行 makemigrations 自动生成 |
| `cvat-ui/src/components/inference-page/index.tsx` | **新建** | 推理任务管理页面 |
| `cvat-ui/src/routes.tsx` | **修改** | 添加 `/inference` 路由 |
| `cvat-ui/src/components/header/header.tsx` | **修改** | 添加导航菜单入口 |

---

## 三、后端改动详解

### 3.1 `cvat/apps/engine/models.py` — 末尾追加

```python
# ── 新增：推理任务 ──────────────────────────────────────────────
class InferenceTaskStatus(str, Enum):
    PENDING   = 'pending'
    RUNNING   = 'running'
    COMPLETED = 'completed'
    FAILED    = 'failed'
    CANCELLED = 'cancelled'

    @classmethod
    def choices(cls):
        return tuple((x.value, x.name) for x in cls)

    def __str__(self):
        return self.value


class InferenceTask(TimestampedModel):
    """
    推理任务：对一个已有 Task 的所有帧执行批量推理，
    结果自动写回该 Task 的标注。
    """
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='inference_tasks',
    )
    # Nuclio 函数 ID，例如 "pth.ultralytics.yolov8.detector"
    function_id  = models.CharField(max_length=256)
    threshold    = models.FloatField(default=0.5)
    cleanup      = models.BooleanField(default=False)   # 推理前是否清空已有标注
    mapping      = models.JSONField(default=dict)        # 标签映射

    status    = models.CharField(
        max_length=32,
        choices=InferenceTaskStatus.choices(),
        default=InferenceTaskStatus.PENDING,
    )
    progress  = models.PositiveIntegerField(default=0)   # 0-100
    rq_job_id = models.CharField(max_length=256, blank=True, default='')
    error     = models.TextField(blank=True, default='')

    owner = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='inference_tasks',
    )
    organization = models.ForeignKey(
        'organizations.Organization', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='inference_tasks',
    )

    class Meta:
        default_permissions = ()
        ordering = ['-created_date']
```

然后运行：
```bash
python manage.py makemigrations engine --name="add_inference_task"
python manage.py migrate
```

---

### 3.2 `cvat/apps/engine/inference.py` — 新建文件

```python
"""
推理任务 RQ 后台执行逻辑。
直接复用 lambda_manager 的 LambdaGateway 调用 Nuclio 函数。
"""
from __future__ import annotations
import traceback

from rq import get_current_job

from cvat.apps.engine.log import ServerLogManager
from cvat.apps.engine.models import InferenceTask, InferenceTaskStatus
from cvat.apps.lambda_manager.views import LambdaGateway

slogger = ServerLogManager(__name__)


def run_inference_task(inference_task_id: int) -> None:
    """RQ 工作函数：执行批量推理并将结果写回 Task 标注。"""
    db_inference = InferenceTask.objects.select_related('task').get(id=inference_task_id)

    try:
        db_inference.status = InferenceTaskStatus.RUNNING
        db_inference.save(update_fields=['status', 'updated_date'])

        # 复用现有 LambdaGateway 获取 Nuclio 函数
        gateway = LambdaGateway()
        func = gateway.get(db_inference.function_id)

        # 构造调用参数（与 lambda_manager offline 模式一致）
        payload = {
            'task':      db_inference.task_id,
            'threshold': db_inference.threshold,
            'cleanup':   db_inference.cleanup,
            'mapping':   db_inference.mapping,
        }

        # invoke 内部会自动遍历所有帧并将检测结果写回 Task 标注
        func.invoke(db_task=db_inference.task, data=payload)

        db_inference.status   = InferenceTaskStatus.COMPLETED
        db_inference.progress = 100
        db_inference.save(update_fields=['status', 'progress', 'updated_date'])

    except Exception:
        db_inference.status = InferenceTaskStatus.FAILED
        db_inference.error  = traceback.format_exc()
        db_inference.save(update_fields=['status', 'error', 'updated_date'])
        raise
```

---

### 3.3 `cvat/apps/engine/serializers.py` — 末尾追加

```python
# ── 新增：推理任务序列化器 ──────────────────────────────────────
from cvat.apps.engine.models import InferenceTask, InferenceTaskStatus

class InferenceTaskWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model  = InferenceTask
        fields = ['task', 'function_id', 'threshold', 'cleanup', 'mapping']

    def validate_threshold(self, value):
        if not 0.0 <= value <= 1.0:
            raise serializers.ValidationError("threshold must be between 0.0 and 1.0")
        return value


class InferenceTaskReadSerializer(serializers.ModelSerializer):
    owner = BasicUserSerializer(read_only=True)

    class Meta:
        model  = InferenceTask
        fields = [
            'id', 'task', 'function_id', 'threshold', 'cleanup', 'mapping',
            'status', 'progress', 'rq_job_id', 'error',
            'owner', 'created_date', 'updated_date',
        ]
        read_only_fields = fields
```

---

### 3.4 `cvat/apps/engine/views.py` — 末尾追加

```python
# ── 新增：推理任务 ViewSet ──────────────────────────────────────
import django_rq
from cvat.apps.engine.inference import run_inference_task
from cvat.apps.engine.models import InferenceTask, InferenceTaskStatus
from cvat.apps.engine.serializers import (
    InferenceTaskReadSerializer,
    InferenceTaskWriteSerializer,
)


@extend_schema(tags=['inference'])
class InferenceTaskViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = InferenceTask.objects.select_related('task', 'owner')
    ordering  = '-id'

    def get_serializer_class(self):
        if self.request.method in SAFE_METHODS:
            return InferenceTaskReadSerializer
        return InferenceTaskWriteSerializer

    def get_queryset(self):
        qs  = super().get_queryset()
        org = self.request.iam_context.get('organization')
        return qs.filter(organization=org) if org else qs.filter(
            owner=self.request.user, organization=None
        )

    @transaction.atomic
    def perform_create(self, serializer):
        instance = serializer.save(
            owner=self.request.user,
            organization=self.request.iam_context.get('organization'),
            status=InferenceTaskStatus.PENDING,
        )
        # 入队到现有 annotation 队列，复用现有 Worker
        queue     = django_rq.get_queue('annotation')
        rq_job    = queue.enqueue(run_inference_task, instance.id)
        instance.rq_job_id = rq_job.id
        instance.save(update_fields=['rq_job_id'])

    def perform_destroy(self, instance):
        if instance.rq_job_id:
            try:
                queue  = django_rq.get_queue('annotation')
                rq_job = queue.fetch_job(instance.rq_job_id)
                if rq_job and rq_job.get_status() in ('queued', 'started'):
                    rq_job.cancel()
            except Exception:
                pass
        instance.delete()
```

---

### 3.5 `cvat/apps/engine/urls.py` — 添加一行

在已有 `router.register` 行之后添加：

```python
router.register(
    r'inference/tasks',
    views.InferenceTaskViewSet,
    basename='inference_task',
)
```

生成的 API 端点：
```
GET    /api/inference/tasks/       # 列表
POST   /api/inference/tasks/       # 创建并入队
GET    /api/inference/tasks/{id}/  # 查询状态
DELETE /api/inference/tasks/{id}/  # 取消/删除
```

---

### 3.6 `cvat/apps/engine/rules/inference_tasks.rego` — 新建文件

```rego
package inference_tasks

import rego.v1

default allow := false

allow if { input.auth.user.privilege == "admin" }

allow if {
    input.scope in {"list", "create"}
    input.auth.user.id != null
}

allow if {
    input.scope in {"view", "delete"}
    input.auth.user.id == input.resource.owner.id
}

allow if {
    input.scope in {"view", "delete"}
    input.auth.organization.id == input.resource.organization.id
    input.auth.org_role in {"maintainer", "owner"}
}
```

---

## 四、前端改动详解

### 4.1 新建 `cvat-ui/src/components/inference-page/index.tsx`

页面功能：
- 表格展示推理任务列表（ID、Task ID、模型、阈值、状态、进度、创建时间）
- 新建推理任务弹窗（选择 Task ID、模型、阈值）
- 每 5 秒自动轮询刷新运行中任务的状态
- 删除/取消按钮

```tsx
import React, { useEffect, useState } from 'react';
import {
    Button, Table, Tag, Select, InputNumber,
    Form, Modal, Space, Progress, Switch,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import Axios from 'axios';

const STATUS_COLOR: Record<string, string> = {
    pending: 'default', running: 'processing',
    completed: 'success', failed: 'error', cancelled: 'warning',
};

const InferencePage: React.FC = () => {
    const [tasks, setTasks]         = useState<any[]>([]);
    const [functions, setFunctions] = useState<any[]>([]);
    const [loading, setLoading]     = useState(false);
    const [modalOpen, setModalOpen] = useState(false);
    const [form]                    = Form.useForm();

    const fetchTasks = async () => {
        setLoading(true);
        try {
            const res = await Axios.get('/api/inference/tasks');
            setTasks(res.data.results ?? res.data);
        } finally { setLoading(false); }
    };

    const fetchFunctions = async () => {
        const res = await Axios.get('/api/lambda/functions');
        // 只显示 detector 类型的函数
        setFunctions((res.data as any[]).filter(f => f.kind === 'detector'));
    };

    useEffect(() => {
        fetchTasks();
        fetchFunctions();
        const timer = setInterval(fetchTasks, 5000);
        return () => clearInterval(timer);
    }, []);

    const handleCreate = async (values: any) => {
        await Axios.post('/api/inference/tasks', {
            task: values.task_id,
            function_id: values.function_id,
            threshold: values.threshold,
            cleanup: values.cleanup ?? false,
            mapping: {},
        });
        setModalOpen(false);
        form.resetFields();
        fetchTasks();
    };

    const handleDelete = async (id: number) => {
        await Axios.delete(`/api/inference/tasks/${id}`);
        fetchTasks();
    };

    const columns = [
        { title: 'ID',      dataIndex: 'id',          width: 60 },
        { title: 'Task ID', dataIndex: 'task',         width: 80 },
        { title: '模型',    dataIndex: 'function_id',  ellipsis: true },
        { title: '阈值',    dataIndex: 'threshold',    width: 70 },
        {
            title: '状态', dataIndex: 'status', width: 110,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
        },
        {
            title: '进度', dataIndex: 'progress', width: 120,
            render: (p: number, row: any) =>
                row.status === 'running' ? <Progress percent={p} size="small" /> : null,
        },
        {
            title: '创建时间', dataIndex: 'created_date',
            render: (d: string) => new Date(d).toLocaleString(),
        },
        {
            title: '操作', width: 70,
            render: (_: any, row: any) => (
                <Button danger icon={<DeleteOutlined />} size="small"
                    disabled={row.status === 'completed'}
                    onClick={() => handleDelete(row.id)} />
            ),
        },
    ];

    return (
        <div style={{ padding: 24 }}>
            <Space style={{ marginBottom: 16 }}>
                <Button type="primary" icon={<PlusOutlined />}
                    onClick={() => setModalOpen(true)}>新建推理任务</Button>
                <Button icon={<ReloadOutlined />} onClick={fetchTasks}>刷新</Button>
            </Space>

            <Table rowKey="id" loading={loading} dataSource={tasks}
                columns={columns} pagination={{ pageSize: 20 }} />

            <Modal title="新建推理任务" open={modalOpen}
                onCancel={() => setModalOpen(false)}
                onOk={() => form.submit()} okText="创建并执行">
                <Form form={form} layout="vertical" onFinish={handleCreate}>
                    <Form.Item name="task_id" label="Task ID"
                        rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={1} />
                    </Form.Item>
                    <Form.Item name="function_id" label="推理模型"
                        rules={[{ required: true }]}>
                        <Select placeholder="选择检测器函数">
                            {functions.map(f => (
                                <Select.Option key={f.id} value={f.id}>
                                    {f.name || f.id}
                                </Select.Option>
                            ))}
                        </Select>
                    </Form.Item>
                    <Form.Item name="threshold" label="置信度阈值" initialValue={0.5}>
                        <InputNumber min={0} max={1} step={0.05}
                            style={{ width: '100%' }} />
                    </Form.Item>
                    <Form.Item name="cleanup" label="推理前清空已有标注"
                        valuePropName="checked" initialValue={false}>
                        <Switch />
                    </Form.Item>
                </Form>
            </Modal>
        </div>
    );
};

export default InferencePage;
```

---

### 4.2 `cvat-ui/src/routes.tsx` — 添加路由

```tsx
import InferencePage from 'components/inference-page';

// 在 <Switch> 内添加：
<Route exact path="/inference" component={InferencePage} />
```

### 4.3 导航菜单 — 添加入口

在侧边栏或顶部导航文件中添加：

```tsx
<Menu.Item key="inference" icon={<ThunderboltOutlined />}>
    <Link to="/inference">推理任务</Link>
</Menu.Item>
```

---

## 五、如何新增自定义模型

CVAT 的模型通过 **Nuclio 函数** 对接。整个机制分两层：

```
你的 .pt 模型文件
  ↓
func.py（推理逻辑，调用模型，返回检测框）
  ↓
cvat-cli function create-native（注册到 CVAT，生成 function_id）
  ↓
cvat-agent 进程（常驻后台，监听 CVAT 发来的推理请求，调用 func.py）
  ↓
CVAT 推理页面下拉框中出现该模型
```

有两种方式新增模型：

---

### 方式一：复用现有 YOLO 框架（推荐）

**适用场景：** 你用 Ultralytics 训练的 `.pt` 文件（YOLOv8/v9/v11/v12 等）

**底层原理：**
- `ai-models/detector/yolo/func.py` 已经写好了通用的 YOLO 推理逻辑
- 你只需要告诉它用哪个 `.pt` 文件，不需要改任何代码
- `docker-compose.yaml` 负责启动注册容器和 Agent 容器

**完整操作步骤：**

#### 第 1 步：准备目录

在项目根目录下，复制一份 yolo 部署目录：

```bash
cp -r ai-models/agents_deployment/yolo  ai-models/agents_deployment/my-detector
cd ai-models/agents_deployment/my-detector
```

目录结构（不需要改任何 `.sh` 或 `docker-compose` 文件）：
```
my-detector/
├── .env                          ← 只改这一个文件
├── check_env.sh                  ← 不动
├── docker-compose.yaml           ← 不动
├── docker-compose-mount-userdata.yaml  ← 不动（挂载本地模型时用）
├── Dockerfile                    ← 不动
├── entrypoint.sh                 ← 不动
├── function_registration.sh      ← 不动
└── function_deregistration.sh    ← 不动
```

#### 第 2 步：先构建 Docker 镜像

```bash
# 在项目根目录执行（因为 Dockerfile 里有 COPY ai-models/detector/yolo/ 路径）
docker build \
  -f ai-models/agents_deployment/yolo/Dockerfile \
  -t my-yolo-agent:latest \
  .
```

> 如果需要 GPU 支持：
> ```bash
> docker build \
>   -f ai-models/agents_deployment/yolo/Dockerfile \
>   --build-arg USE_GPU=true \
>   -t my-yolo-agent-gpu:latest \
>   .
> ```

#### 第 3 步：获取 CVAT API Token

在 CVAT 界面：`右上角头像` → `Profile` → `Security` → `Create token`

复制生成的 token，例如：`abc123def456...`

#### 第 4 步：修改 `.env` 文件

打开 `ai-models/agents_deployment/my-detector/.env`，按以下说明修改：

```bash
# ── 必填项 ────────────────────────────────────────────────────

# 你的 CVAT 服务地址（本地部署通常是这个）
CVAT_BASE_URL=http://localhost:8080

# 第 3 步获取的 Token
CVAT_ACCESS_TOKEN=abc123def456...

# 这个名字会显示在推理页面的模型下拉框里，随便起，不能有空格
FUNCTION_NAME=my-yolo-v8-detector

# 第 2 步构建的镜像名
IMAGE_URL=my-yolo-agent:latest

# ── 模型配置 ──────────────────────────────────────────────────

# 情况 A：使用 Ultralytics 官方预训练模型（会自动下载）
="-p model=str:yolov8n.pt"

# 情况 B：使用你自己训练的本地模型（需要同时设置下面两行）
="-p model=str:/app/data/my_custom.pt"
USER_DATA_ABSOLUTE_PATH=/your/local/path/to/model/directory
COMPOSE_FILE=docker-compose.yaml:docker-compose-mount-userdata.yaml

# ── 可选项 ────────────────────────────────────────────────────

# 并发 Agent 数量（一个 Agent 同时处理一个推理请求）
AGENTS_COUNT=1

# 组织 slug（如果你在某个 Organization 下使用，填组织的短名称）
ORG_SLUG=

# 是否对所有用户可见（public = 所有人可用，private = 仅自己和组织成员）
FUNCTION_VISIBILITY=private

# GPU 支持（需要 NVIDIA GPU + 驱动 + nvidia-container-toolkit）
# cpu = 不用 GPU，gpu = 使用 GPU
COMPOSE_PROFILES=cpu
```

**情况 B 的目录结构示例：**
```
/your/local/path/to/model/directory/
└── my_custom.pt          ← 你的模型文件
```
容器内会挂载为 `/app/data/`，所以路径写 `/app/data/my_custom.pt`。

#### 第 5 步：注册函数并启动 Agent

```bash
cd ai-models/agents_deployment/my-detector

# 注册函数（只需运行一次，成功后会在 CVAT 里创建函数记录）
docker compose up cvat-function-register

# 查看注册日志，成功时会显示：
# Successfully created my-yolo-v8-detector function
# 并输出 FUNCTION_ID（一个数字）

# 启动 Agent（常驻后台，负责实际执行推理）
docker compose up -d cvat-agent
```

#### 第 6 步：验证

```bash
# 查看函数是否注册成功
curl http://localhost:8080/api/lambda/functions \
  -H "Authorization: Token abc123def456..."

# 应该能看到你的函数：
# {"id": "my-yolo-v8-detector", "kind": "detector", "name": "my-yolo-v8-detector", ...}
```

打开推理页面，新建推理任务时，模型下拉框里应该出现 `my-yolo-v8-detector`。

#### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 注册失败，提示 401 | Token 错误或过期 | 重新生成 Token |
| 注册失败，提示连接拒绝 | CVAT_BASE_URL 错误 | 检查地址和端口 |
| 模型下载超时 | 网络问题 | 改用情况 B，手动下载 `.pt` 文件 |
| Agent 启动后立即退出 | FUNCTION_ID 未写入 | 先确认注册步骤成功 |
| 推理结果为空 | 标签名不匹配 | 见下方"标签映射"说明 |

#### 标签映射说明

YOLO 模型的标签名（如 `person`、`car`）必须与 CVAT Task 里定义的标签名**完全一致**，推理结果才能正确写入。

如果名字不一致，在推理页面创建任务时，`mapping` 字段可以做映射：
```json
{
  "person": {"name": "人"},
  "car":    {"name": "车辆"}
}
```

---

### 方式二：编写自定义 `func.py`（适用于非 YOLO 模型）

**适用场景：** PyTorch 自定义模型、ONNX 模型、其他框架

#### 第 1 步：新建目录

```
ai-models/detector/my_model/
├── func.py           # 推理逻辑（必须）
├── requirements.txt  # Python 依赖（必须）
└── README.md         # 说明（可选）
```

#### 第 2 步：编写 `func.py`

`func.py` 必须实现一个 `create()` 函数，返回一个带 `spec` 属性和 `detect()` 方法的对象：

```python
import cvat_sdk.auto_annotation as cvataa
import PIL.Image
import torch

class MyCustomDetector:
    def __init__(self, model_path: str):
        self.model = torch.load(model_path, map_location='cpu')
        self.model.eval()

        # 声明模型支持的标签
        # 标签名必须与 CVAT Task 里定义的标签名一致
        self.spec = cvataa.DetectionFunctionSpec(
            labels=[
                cvataa.label_spec("cat",  0, type="rectangle"),
                cvataa.label_spec("dog",  1, type="rectangle"),
            ]
        )

    def detect(
        self,
        context: cvataa.DetectionFunctionContext,
        image: PIL.Image.Image,
    ) -> list[cvataa.DetectionAnnotation]:
        import numpy as np
        img_array = np.array(image)

        with torch.no_grad():
            results = self.model(img_array)

        annotations = []
        for box, label_id, conf in zip(results['boxes'], results['labels'], results['scores']):
            if context.conf_threshold and conf.item() < context.conf_threshold:
                continue
            x1, y1, x2, y2 = box.tolist()
            annotations.append(
                cvataa.rectangle(int(label_id.item()), [x1, y1, x2, y2])
            )
        return annotations


def create(model_path: str) -> cvataa.DetectionFunction:
    """CVAT 调用这个函数初始化模型，参数名对应 -p model_path=str:xxx"""
    return MyCustomDetector(model_path)
```

#### 第 3 步：编写 `requirements.txt`

```
cvat-sdk
torch
torchvision
Pillow
numpy
```

#### 第 4 步：注册函数

```bash
# 安装 cvat-cli（如果还没装）
pip install cvat-sdk

# 注册函数
cvat-cli \
  --server-host http://localhost:8080 \
  --auth your_username:your_password \
  function create-native "my-custom-detector" \
  --function-file ai-models/detector/my_model/func.py \
  -p model_path=str:/absolute/path/to/your/model.pth
```

注册成功后输出函数 ID，之后推理页面下拉框里就能看到它。

> **注意：** 方式二注册的函数没有常驻 Agent，每次推理时 CVAT 会直接在注册时的进程里调用 `func.py`。适合测试，生产环境建议用方式一的 Agent 模式。

---

### 两种方式对比

| | 方式一（复用 YOLO 框架） | 方式二（自定义 func.py） |
|--|--|--|
| **适用场景** | Ultralytics YOLO 系列 `.pt` 文件 | 任意框架的模型 |
| **工作量** | 改 `.env` + 构建镜像，约 10 分钟 | 编写 `func.py`，约 1-2 小时 |
| **需要改代码** | 不需要 | 需要 |
| **运行方式** | 常驻 Agent 进程（生产推荐） | 直接调用（适合测试） |
| **GPU 支持** | 内置支持，改 `COMPOSE_PROFILES=gpu` | 自己在 `func.py` 里处理 |
| **多模型并发** | 每个模型独立 Agent，互不干扰 | 共享进程 |

---

## 六、实施顺序

```
Day 1（后端）
  1. 修改 models.py → 运行 makemigrations + migrate
  2. 新建 inference.py
  3. 修改 serializers.py
  4. 修改 views.py
  5. 修改 urls.py
  6. 新建 inference_tasks.rego
  7. 用 curl 验证 API：
     curl -X POST http://localhost:8080/api/inference/tasks \
       -H "Authorization: Token xxx" \
       -d '{"task":1,"function_id":"xxx","threshold":0.5}'

Day 2（前端）
  1. 新建 inference-page/index.tsx
  2. 修改 routes.tsx
  3. 修改导航菜单
  4. 联调测试

Day 3（新增模型，可选）
  1. 准备 .pt 文件
  2. 修改 .env
  3. docker compose up cvat-function-register
  4. 在推理页面验证模型出现在下拉列表中
```

---

## 七、关键复用点总结

| 复用内容 | 来源位置 | 说明 |
|---------|---------|------|
| 模型调用 | `lambda_manager.views.LambdaGateway` | 直接 import，无需重写 |
| 标注写回 | `lambda_manager.views.LambdaFunction.invoke()` | 自动处理标签映射和写回 Task |
| 异步队列 | `annotation` RQ 队列 | 无需新增队列，复用现有 Worker 容器 |
| 权限体系 | `iam.permissions` | 继承 OPA 机制 |
| 模型部署 | `ai-models/agents_deployment/yolo/` | 复用现有部署脚本 |
