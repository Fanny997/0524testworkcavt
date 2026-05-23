# 示例：图片模糊增强

这是一个可直接部署的 Nuclio 数据增强示例。

## 基础镜像

先在项目根目录构建一次基础镜像：

```powershell
docker build `
  -f custom_operations_registry/base-images/nuclio-onnxruntime/Dockerfile `
  -t cvat/nuclio-onnxruntime:dev `
  custom_operations_registry/base-images/nuclio-onnxruntime
```

`function.yaml` 已经引用：

```yaml
baseImage: cvat/nuclio-onnxruntime:dev
```

## 功能

- 输入：一个图片集合。
- 参数：模糊半径、输出格式。
- 输出：一个图片集合，每张输入图片对应一张增强后的图片。

## 调用流程

1. 在“工作流”页面选择“数据增强”。
2. 选择“示例：图片模糊增强”。
3. 上传一张或多张图片。
4. 点击“运行当前步骤”或加入工作流后点击“运行工作流”。

## 说明

Nuclio 容器不直接写 Windows 宿主机路径。示例函数会把输出图片编码成 base64 返回给 CVAT 后端，后端再把图片保存到工作流输出目录。
