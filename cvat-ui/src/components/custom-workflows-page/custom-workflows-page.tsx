// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import './styles.scss';

import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { useHistory } from 'react-router';
import {
    getCore,
    CustomOperationKind,
    SerializedCustomOperation,
    SerializedCustomOperationField,
} from 'cvat-core-wrapper';
import notification from 'antd/lib/notification';
import Tabs from 'antd/lib/tabs';
import Button from 'antd/lib/button';
import Input from 'antd/lib/input';
import InputNumber from 'antd/lib/input-number';
import Select from 'antd/lib/select';
import Switch from 'antd/lib/switch';
import Upload from 'antd/lib/upload';
import Drawer from 'antd/lib/drawer';
import Empty from 'antd/lib/empty';
import Space from 'antd/lib/space';
import Tag from 'antd/lib/tag';
import Divider from 'antd/lib/divider';
import Spin from 'antd/lib/spin';
import Text from 'antd/lib/typography/Text';
import Tooltip from 'antd/lib/tooltip';
import Modal from 'antd/lib/modal';
import Image from 'antd/lib/image';
import Pagination from 'antd/lib/pagination';
import Descriptions from 'antd/lib/descriptions';
import {
    UploadOutlined,
    PlusOutlined,
    ReloadOutlined,
    PlayCircleOutlined,
    DeleteOutlined,
    DownloadOutlined,
    EyeOutlined,
    FileTextOutlined,
    FileImageOutlined,
    FileOutlined,
    EditOutlined,
    DownOutlined,
    UpOutlined,
    UndoOutlined,
    FilePdfOutlined,
    FileExcelOutlined,
    FilePptOutlined,
    FileZipOutlined,
    FileWordOutlined,
    FileUnknownOutlined,
} from '@ant-design/icons';

type WorkflowStepStatus = 'idle' | 'running' | 'success' | 'failed';
const OUTPUT_PATH_FIELD = '_output_path';
const SAVE_OUTPUTS_FIELD = '_save_outputs';
const STATUS_LABELS: Record<WorkflowStepStatus, string> = {
    idle: '待运行',
    running: '运行中',
    success: '成功',
    failed: '失败',
};
const OPERATION_KIND_LABELS: Record<CustomOperationKind, string> = {
    model: '模型',
    augmentation: '数据增强',
};

interface WorkflowStep {
    id: string;
    operation: SerializedCustomOperation;
    values: Record<string, any>;
    status: WorkflowStepStatus;
    result: any;
    error: string | null;
}

interface DraftOperation {
    name: string;
    kind: CustomOperationKind;
    description: string;
    nuclio_function: string;
    input_schema: string;
    output_schema: string;
    is_active: boolean;
    artifact: File | null;
}

interface PreviousStepOption {
    label: string;
    stepIndex: number;
}

function makeId(): string {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function safeParseJSON(raw: string, fallback: any): any {
    if (!raw.trim()) {
        return fallback;
    }

    return JSON.parse(raw);
}

function buildDefaultValues(operation: SerializedCustomOperation | null): Record<string, any> {
    if (!operation) return {};

    const values = operation.input_schema.reduce<Record<string, any>>((acc, field) => {
        if (Object.prototype.hasOwnProperty.call(field, 'default')) {
            acc[field.name] = field.default;
        } else if (field.type === 'boolean') {
            acc[field.name] = false;
        } else if (field.type === 'file') {
            acc[field.name] = null;
        } else if (field.type === 'file_collection') {
            acc[field.name] = [];
        } else {
            acc[field.name] = '';
        }
        return acc;
    }, {});
    values[OUTPUT_PATH_FIELD] = '';
    values[SAVE_OUTPUTS_FIELD] = true;
    return values;
}

function prettyJSON(value: any): string {
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

// ---- 输出文件相关类型与工具 ----

interface OutputFileItem {
    name: string;
    url: string;
    type: 'image' | 'text' | 'other';
    mimeType?: string;
    size?: number;
}

const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico'];
const TEXT_EXTENSIONS = ['.txt', '.json', '.csv', '.xml', '.yaml', '.yml', '.md',
    '.log', '.py', '.js', '.ts', '.html', '.css', '.scss', '.sh', '.bat', '.cfg', '.ini'];

function detectFileType(filename: string, mimeType?: string): OutputFileItem['type'] {
    const dotIndex = filename.lastIndexOf('.');
    const ext = dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : '';
    if (IMAGE_EXTENSIONS.includes(ext)) return 'image';
    if (TEXT_EXTENSIONS.includes(ext)) return 'text';
    if (mimeType) {
        if (mimeType.startsWith('image/')) return 'image';
        if (mimeType.startsWith('text/')) return 'text';
    }
    return 'other';
}

function isString(value: any): value is string {
    return typeof value === 'string';
}

function looksLikeURL(value: string): boolean {
    return /^https?:\/\//i.test(value) || value.startsWith('/');
}

function resolveDownloadURL(rawUrl: string): string {
    if (/^https?:\/\//i.test(rawUrl)) return rawUrl;
    const origin = window.location.origin;
    return rawUrl.startsWith('/') ? `${origin}${rawUrl}` : `${origin}/${rawUrl}`;
}

function extractOutputFiles(data: any): OutputFileItem[] {
    const files: OutputFileItem[] = [];
    const seen = new Set<string>();

    function walk(obj: any): void {
        if (!obj || typeof obj !== 'object') return;
        if (Array.isArray(obj)) {
            obj.forEach(walk);
            return;
        }

        // 尝试识别文件对象的常见字段
        const name = obj.name || obj.filename || obj.file_name || obj.key;
        const url = obj.url || obj.path || obj.download_url || obj.href;
        const mimeType = obj.mime_type || obj.mimeType || obj.type || obj.content_type;

        if (isString(name) && isString(url) && looksLikeURL(url) && !seen.has(url)) {
            seen.add(url);
            files.push({
                name,
                url,
                type: detectFileType(name, mimeType),
                mimeType,
                size: obj.size ?? obj.file_size,
            });
        }

        // 遍历嵌套结构：output_collection / results / items / files 等
        const nested = obj.output_collection || obj.results || obj.items ||
            obj.data || obj.files || obj.outputs;
        if (nested) walk(nested);

        // 也检查所有属性值
        Object.values(obj).forEach((val) => {
            if (val && typeof val === 'object' && val !== obj && !seen.has(JSON.stringify(val))) {
                walk(val);
            }
        });
    }

    walk(data);
    return files;
}

const OUTPUT_PAGE_SIZE = 15; // 每页 15 张图片（3 行 x 5 列）

function prepareStepValues(
    values: Record<string, any>,
    stepResults: any[],
    stepIndex: number,
): Record<string, any> {
    const nextValues = Object.fromEntries(
        Object.entries(values).filter(([key]) => !key.startsWith('_')),
    );
    for (const [key, value] of Object.entries(nextValues)) {
        if (
            typeof value === 'object' &&
            value !== null &&
            (value as Record<string, any>).source === 'workflow_step'
        ) {
            const sourceStepIndex = Number((value as Record<string, any>).stepIndex);
            if (sourceStepIndex >= stepIndex) {
                throw new Error(
                    `第 ${stepIndex + 1} 步的输入“${key}”只能选择更早步骤的输出`,
                );
            }
            const sourceResult = stepResults[sourceStepIndex];
            const runId = sourceResult?.run?.id || sourceResult?.output_collection?.run_id;
            if (!runId) {
                throw new Error(
                    `第 ${stepIndex + 1} 步的输入“${key}”需要第 ${sourceStepIndex + 1} 步先产生输出`,
                );
            }
            nextValues[key] = { source: 'run', run_id: runId };
        }
    }
    return nextValues;
}

function OperationFieldEditor(props: {
    field: SerializedCustomOperationField;
    value: any;
    onChange: (value: any) => void;
    previousStepOptions?: PreviousStepOption[];
}): JSX.Element {
    const {
        field, value, onChange, previousStepOptions = [],
    } = props;
    const label = field.label || field.name;
    const sourceMode = value?.source === 'workflow_step' ? `step:${value.stepIndex}` : 'upload';
    const renderSourceSelector = field.type === 'file_collection' && previousStepOptions.length > 0;

    let control: JSX.Element;
    switch (field.type) {
        case 'number':
        case 'integer':
            control = (
                <InputNumber
                    value={typeof value === 'number' ? value : undefined}
                    placeholder={field.placeholder}
                    precision={field.type === 'integer' ? 0 : undefined}
                    min={field.minimum}
                    max={field.maximum}
                    step={field.step}
                    style={{ width: '100%' }}
                    onChange={(nextValue) => onChange(nextValue ?? '')}
                />
            );
            break;
        case 'boolean':
            control = (
                <Switch checked={!!value} onChange={(checked) => onChange(checked)} />
            );
            break;
        case 'select':
            control = (
                <Select
                    value={value}
                    placeholder={field.placeholder}
                    onChange={(nextValue) => onChange(nextValue)}
                    options={(field.options || []).map((option) => ({
                        label: option.label,
                        value: option.value,
                    }))}
                />
            );
            break;
        case 'json':
            control = (
                <Input.TextArea
                    value={typeof value === 'string' ? value : prettyJSON(value)}
                    placeholder={field.placeholder || '{"key":"value"}'}
                    autoSize={{ minRows: 4, maxRows: 10 }}
                    onChange={(event) => onChange(event.target.value)}
                />
            );
            break;
        case 'file':
            control = (
                <Upload
                    maxCount={1}
                    accept={field.accept?.join(',')}
                    beforeUpload={() => false}
                    fileList={value ? [{
                        uid: 'selected-file',
                        name: value.name || '已选文件',
                        status: 'done' as const,
                        originFileObj: value as any,
                    }] : []}
                    onChange={(info) => onChange(info.file.originFileObj || null)}
                >
                    <Button icon={<UploadOutlined />}>{value ? '替换文件' : '选择文件'}</Button>
                </Upload>
            );
            break;
        case 'file_collection':
            if (sourceMode.startsWith('step:')) {
                control = (
                    <Text type='secondary'>
                        将使用选中的前序步骤输出集合作为当前输入。
                    </Text>
                );
            } else {
                const files = Array.isArray(value) ? value : [];
                control = (
                    <Upload
                        multiple
                        maxCount={field.max_count}
                        accept={field.accept?.join(',')}
                        beforeUpload={() => false}
                        fileList={files.map((file: File, index: number) => ({
                            uid: `${file.name}-${index}`,
                            name: file.name || `文件-${index + 1}`,
                            status: 'done' as const,
                            originFileObj: file as any,
                        }))}
                        onChange={(info) => onChange(info.fileList
                            .map((item) => item.originFileObj)
                            .filter((item) => !!item))}
                    >
                        <Button icon={<UploadOutlined />}>
                            {files.length ? `已选择 ${files.length} 个文件` : '选择文件'}
                        </Button>
                    </Upload>
                );
            }
            break;
        case 'text':
            control = (
                <Input.TextArea
                    value={value}
                    placeholder={field.placeholder}
                    autoSize={{ minRows: 3, maxRows: 8 }}
                    onChange={(event) => onChange(event.target.value)}
                />
            );
            break;
        case 'string':
        default:
            control = (
                <Input
                    value={value}
                    placeholder={field.placeholder}
                    onChange={(event) => onChange(event.target.value)}
                />
            );
            break;
    }

    return (
        <div className='cvat-workflow-field'>
            <div className='cvat-workflow-field-label'>
                <Text strong>{label}</Text>
                {field.required ? <Tag color='red'>必填</Tag> : null}
            </div>
            {field.description ? <Text type='secondary'>{field.description}</Text> : null}
            {renderSourceSelector ? (
                <Select
                    value={sourceMode}
                    style={{ width: '100%', marginBottom: 8 }}
                    onChange={(nextSource) => {
                        if (nextSource === 'upload') {
                            onChange([]);
                            return;
                        }

                        const stepIndex = Number(nextSource.replace('step:', ''));
                        onChange({ source: 'workflow_step', stepIndex });
                    }}
                    options={[
                        { label: '上传文件', value: 'upload' },
                        ...previousStepOptions.map((option) => ({
                            label: `使用${option.label}输出`,
                            value: `step:${option.stepIndex}`,
                        })),
                    ]}
                />
            ) : null}
            {control}
        </div>
    );
}

// ---- 图片输出画廊 ----

function ImageOutputGallery(props: {
    images: OutputFileItem[];
}): JSX.Element {
    const { images } = props;
    const [currentPage, setCurrentPage] = useState(1);
    const needPagination = images.length > OUTPUT_PAGE_SIZE;
    const startIdx = (currentPage - 1) * OUTPUT_PAGE_SIZE;
    const pageImages = needPagination ? images.slice(startIdx, startIdx + OUTPUT_PAGE_SIZE) : images;

    return (
        <>
            <Image.PreviewGroup>
                <div className='cvat-workflow-image-grid'>
                    {pageImages.map((img) => (
                        <div key={img.url} className='cvat-workflow-image-item'>
                            <Image
                                src={img.url}
                                alt={img.name}
                                fallback='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDgiIGhlaWdodD0iNDgiIHZpZXdCb3g9IjAgMCA0OCA0OCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iNDgiIGhlaWdodD0iNDgiIHJ4PSI0IiBmaWxsPSIjZjBmMGYwIi8+PHRleHQgeD0iMjQiIHk9IjI4IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjYmZiZmJmIiBmb250LXNpemU9IjEyIj7lm77niYc8L3RleHQ+PC9zdmc+'
                                preview={{ mask: <EyeOutlined /> }}
                            />
                            <div className='cvat-workflow-image-name'>
                                <Tooltip title={img.name}>
                                    {img.name}
                                </Tooltip>
                            </div>
                        </div>
                    ))}
                </div>
            </Image.PreviewGroup>
            {needPagination ? (
                <div className='cvat-workflow-output-pagination'>
                    <Pagination
                        current={currentPage}
                        pageSize={OUTPUT_PAGE_SIZE}
                        total={images.length}
                        showSizeChanger={false}
                        size='small'
                        onChange={(page) => setCurrentPage(page)}
                    />
                </div>
            ) : null}
        </>
    );
}

// ---- 文本文件展示 ----

function TextOutputFiles(props: {
    files: OutputFileItem[];
    onViewContent: (file: OutputFileItem) => void;
}): JSX.Element {
    const { files, onViewContent } = props;
    const [currentPage, setCurrentPage] = useState(1);
    const needPagination = files.length > 10;
    const startIdx = (currentPage - 1) * 10;
    const pageFiles = needPagination ? files.slice(startIdx, startIdx + 10) : files;

    const handleDownload = (e: React.MouseEvent, url: string, name: string) => {
        e.stopPropagation();
        const downloadUrl = resolveDownloadURL(url);
        const anchor = document.createElement('a');
        anchor.href = downloadUrl;
        anchor.download = name;
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
    };

    const fileIcon = (fileName: string) => {
        const ext = fileName.slice(fileName.lastIndexOf('.')).toLowerCase();
        if (['.json', '.xml', '.yaml', '.yml'].includes(ext)) {
            return <FileTextOutlined className='cvat-workflow-file-card-icon' />;
        }
        if (['.py', '.js', '.ts', '.sh', '.bat'].includes(ext)) {
            return <FileTextOutlined className='cvat-workflow-file-card-icon' style={{ color: '#722ed1' }} />;
        }
        if (['.pdf'].includes(ext)) {
            return <FilePdfOutlined className='cvat-workflow-file-card-icon' style={{ color: '#ff4d4f' }} />;
        }
        if (['.xlsx', '.xls', '.csv'].includes(ext)) {
            return <FileExcelOutlined className='cvat-workflow-file-card-icon' style={{ color: '#52c41a' }} />;
        }
        if (['.docx', '.doc'].includes(ext)) {
            return <FileWordOutlined className='cvat-workflow-file-card-icon' style={{ color: '#1890ff' }} />;
        }
        if (['.pptx', '.ppt'].includes(ext)) {
            return <FilePptOutlined className='cvat-workflow-file-card-icon' style={{ color: '#fa8c16' }} />;
        }
        if (['.zip', '.gz', '.tar', '.rar', '.7z'].includes(ext)) {
            return <FileZipOutlined className='cvat-workflow-file-card-icon' style={{ color: '#8c8c8c' }} />;
        }
        if (['.txt', '.log', '.md', '.cfg', '.ini'].includes(ext)) {
            return <FileTextOutlined className='cvat-workflow-file-card-icon' />;
        }
        return <FileUnknownOutlined className='cvat-workflow-file-card-icon' />;
    };

    return (
        <>
            <div className='cvat-workflow-text-files'>
                {pageFiles.map((file) => (
                    <div key={file.url} className='cvat-workflow-file-card'>
                        <div className='cvat-workflow-file-card-left'>
                            {fileIcon(file.name)}
                            <Tooltip title={file.name}>
                                <span className='cvat-workflow-file-card-name'>{file.name}</span>
                            </Tooltip>
                        </div>
                        <Space size='small'>
                            <Button
                                size='small'
                                type='link'
                                icon={<EyeOutlined />}
                                onClick={() => onViewContent(file)}
                            >
                                查看
                            </Button>
                            <Button
                                size='small'
                                type='link'
                                icon={<DownloadOutlined />}
                                onClick={(e) => handleDownload(e, file.url, file.name)}
                            >
                                下载
                            </Button>
                        </Space>
                    </div>
                ))}
            </div>
            {needPagination ? (
                <div className='cvat-workflow-output-pagination'>
                    <Pagination
                        current={currentPage}
                        pageSize={10}
                        total={files.length}
                        showSizeChanger={false}
                        size='small'
                        onChange={(page) => setCurrentPage(page)}
                    />
                </div>
            ) : null}
        </>
    );
}

// ---- 最终输出结果 Tab 内容 ----

function FinalOutputTabContent(props: {
    workflow: WorkflowStep[];
}): JSX.Element {
    const { workflow } = props;

    const outputData = useMemo(() => {
        const allFiles: OutputFileItem[] = [];

        workflow.forEach((step) => {
            if (!step.result) return;
            const files = extractOutputFiles(step.result);
            allFiles.push(...files);
        });

        // 去重
        const unique = new Map<string, OutputFileItem>();
        allFiles.forEach((f) => { unique.set(f.url, f); });
        const deduplicated = Array.from(unique.values());

        return {
            images: deduplicated.filter((f) => f.type === 'image'),
            texts: deduplicated.filter((f) => f.type === 'text'),
            others: deduplicated.filter((f) => f.type === 'other'),
        };
    }, [workflow]);

    const [viewingFile, setViewingFile] = useState<OutputFileItem | null>(null);
    const [fileContent, setFileContent] = useState<string>('');
    const [contentLoading, setContentLoading] = useState(false);

    const handleViewContent = useCallback(async (file: OutputFileItem) => {
        setViewingFile(file);
        setContentLoading(true);
        try {
            const response = await fetch(file.url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const text = await response.text();
            setFileContent(text);
        } catch (error) {
            setFileContent(`无法加载文件内容: ${String(error)}`);
        } finally {
            setContentLoading(false);
        }
    }, []);

    const handleCloseContent = useCallback(() => {
        setViewingFile(null);
        setFileContent('');
    }, []);

    const hasImages = outputData.images.length > 0;
    const hasTexts = outputData.texts.length > 0;
    const hasOthers = outputData.others.length > 0;
    const hasAnyFiles = hasImages || hasTexts || hasOthers;

    // 没有任何文件 → 空状态
    if (!hasAnyFiles) {
        return (
            <div className='cvat-workflow-output-container'>
                <Empty description='暂无输出结果，请先运行工作流步骤' />
            </div>
        );
    }

    return (
        <div className='cvat-workflow-output-container'>
            <div className='cvat-workflow-output-content'>
                {hasImages ? (
                    <div style={{ marginBottom: 24 }}>
                        <div className='cvat-workflow-output-header' style={{ paddingLeft: 0, paddingTop: 0 }}>
                            <Space>
                                <FileImageOutlined style={{ color: '#2ba471' }} />
                                <Text strong>图片 ({outputData.images.length})</Text>
                            </Space>
                        </div>
                        <ImageOutputGallery images={outputData.images} />
                    </div>
                ) : null}

                {hasTexts ? (
                    <div style={{ marginBottom: hasOthers ? 24 : 0 }}>
                        <div className='cvat-workflow-output-header' style={{ paddingLeft: 0, paddingTop: 0 }}>
                            <Space>
                                <FileTextOutlined style={{ color: '#0052d9' }} />
                                <Text strong>文本文件 ({outputData.texts.length})</Text>
                            </Space>
                        </div>
                        <TextOutputFiles
                            files={outputData.texts}
                            onViewContent={handleViewContent}
                        />
                    </div>
                ) : null}

                {hasOthers ? (
                    <div>
                        <div className='cvat-workflow-output-header' style={{ paddingLeft: 0, paddingTop: 0 }}>
                            <Space>
                                <FileOutlined style={{ color: '#8c8c8c' }} />
                                <Text strong>其他文件 ({outputData.others.length})</Text>
                            </Space>
                        </div>
                        <TextOutputFiles
                            files={outputData.others}
                            onViewContent={handleViewContent}
                        />
                    </div>
                ) : null}
            </div>

            <Modal
                title={viewingFile?.name || '文件内容'}
                open={!!viewingFile}
                onCancel={handleCloseContent}
                footer={null}
                width={720}
                destroyOnClose
            >
                {contentLoading ? (
                    <div style={{ textAlign: 'center', padding: 48 }}>
                        <Spin />
                    </div>
                ) : (
                    <pre className='cvat-workflow-text-content'>{fileContent}</pre>
                )}
            </Modal>
        </div>
    );
}

export default function CustomWorkflowsPage(): JSX.Element {
    const history = useHistory();
    const core = getCore();

    const [loading, setLoading] = useState(false);
    const [models, setModels] = useState<SerializedCustomOperation[]>([]);
    const [augmentations, setAugmentations] = useState<SerializedCustomOperation[]>([]);
    const [selectedKind, setSelectedKind] = useState<CustomOperationKind>('model');
    const [selectedOperationId, setSelectedOperationId] = useState<number | null>(null);
    const [editorValues, setEditorValues] = useState<Record<string, any>>({});
    const [workflow, setWorkflow] = useState<WorkflowStep[]>([]);
    const [running, setRunning] = useState(false);
    const [previewResult, setPreviewResult] = useState<any>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [drawerVisible, setDrawerVisible] = useState(false);
    const [draft, setDraft] = useState<DraftOperation>({
        name: '',
        kind: 'model',
        description: '',
        nuclio_function: '',
        input_schema: '[]',
        output_schema: '{}',
        is_active: true,
        artifact: null,
    });
    const selectedOperationIdRef = useRef<number | null>(null);
    const allowManualRegistration = false;

    // 步骤详情弹窗
    const [stepDetailVisible, setStepDetailVisible] = useState(false);
    const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);

    // 步骤结果展开/折叠状态
    const [expandedResults, setExpandedResults] = useState<Set<string>>(new Set());

    // 左右区域拖动调整宽度
    const resizerRef = useRef<HTMLDivElement>(null);
    const [leftWidth, setLeftWidth] = useState(35); // 百分比
    const [isResizing, setIsResizing] = useState(false);

    const handleResizerMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsResizing(true);
    }, []);

    useEffect(() => {
        if (!isResizing) return;

        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'col-resize';

        const handleMouseMove = (e: MouseEvent) => {
            const layout = document.querySelector('.cvat-workflows-layout');
            if (!layout) return;
            const rect = layout.getBoundingClientRect();
            const newWidth = ((e.clientX - rect.left) / rect.width) * 100;
            setLeftWidth(Math.max(22, Math.min(55, newWidth)));
        };

        const handleMouseUp = () => {
            setIsResizing(false);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        };
    }, [isResizing]);

    const toggleResultExpand = useCallback((stepId: string) => {
        setExpandedResults((prev) => {
            const next = new Set(prev);
            if (next.has(stepId)) {
                next.delete(stepId);
            } else {
                next.add(stepId);
            }
            return next;
        });
    }, []);

    // 右侧上下区域拖动调整高度
    const topResizerRef = useRef<HTMLDivElement>(null);
    const [topHeight, setTopHeight] = useState(50); // 百分比
    const [isTopResizing, setIsTopResizing] = useState(false);

    const handleTopResizerMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsTopResizing(true);
    }, []);

    useEffect(() => {
        if (!isTopResizing) return;

        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'row-resize';

        const handleMouseMove = (e: MouseEvent) => {
            const builderPanel = document.querySelector('.cvat-workflow-builder-panel');
            if (!builderPanel) return;
            const rect = builderPanel.getBoundingClientRect();
            const newPercent = ((e.clientY - rect.top) / rect.height) * 100;
            setTopHeight(Math.max(20, Math.min(80, newPercent)));
        };

        const handleMouseUp = () => {
            setIsTopResizing(false);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
        };
    }, [isTopResizing]);

    useEffect(() => {
        selectedOperationIdRef.current = selectedOperationId;
    }, [selectedOperationId]);

    const currentOperations = selectedKind === 'model' ? models : augmentations;
    const selectedOperation = useMemo(
        () => currentOperations.find((operation) => operation.id === selectedOperationId) || null,
        [currentOperations, selectedOperationId],
    );
    const workflowStepOptions = useMemo(() => workflow.map((step, index) => ({
        label: `第 ${index + 1} 步：${step.operation.name}`,
        stepIndex: index,
    })), [workflow]);

    const loadOperations = useCallback(async () => {
        setLoading(true);
        try {
            const [modelOps, augmentationOps] = await Promise.all([
                core.customOperations.list({ kind: 'model' }),
                core.customOperations.list({ kind: 'augmentation' }),
            ]);

            setModels(modelOps);
            setAugmentations(augmentationOps);

            const list = selectedKind === 'model' ? modelOps : augmentationOps;

            // 首次加载时自动选择第一个操作
            if (!initialLoadDoneRef.current) {
                const firstOp = list[0] || null;
                if (firstOp) {
                    setSelectedOperationId(firstOp.id);
                    setEditorValues(buildDefaultValues(firstOp));
                    initialLoadDoneRef.current = true;
                }
            } else {
                // 切换 tab 时：自动选择该 tab 的第一个操作
                // 不清空 workflow，只切换当前显示的操作
                const firstOp = list[0] || null;
                if (firstOp) {
                    setSelectedOperationId(firstOp.id);
                    setEditorValues(buildDefaultValues(firstOp));
                } else {
                    setSelectedOperationId(null);
                    setEditorValues({});
                }
            }
        } catch (error) {
            notification.error({
                message: '无法加载自定义操作',
                description: String(error),
            });
        } finally {
            setLoading(false);
        }
    }, [core, selectedKind]);

    useEffect(() => {
        loadOperations();
    }, [loadOperations]);

    const selectOperation = useCallback((operation: SerializedCustomOperation) => {
        setSelectedKind(operation.kind);
        setSelectedOperationId(operation.id);
        setEditorValues(buildDefaultValues(operation));
        setPreviewError(null);
        setPreviewResult(null);
        initialLoadDoneRef.current = true;
    }, []);

    const handleRunCurrent = useCallback(async () => {
        if (!selectedOperation) return;

        setRunning(true);
        setPreviewError(null);
        try {
            const result = await core.customOperations.execute(
                selectedOperation.id,
                prepareStepValues(editorValues, workflow.map((step) => step.result), workflow.length),
            );
            setPreviewResult(result);
        } catch (error) {
            const message = String(error);
            setPreviewError(message);
            notification.error({
                message: '自定义操作执行失败',
                description: message,
            });
        } finally {
            setRunning(false);
        }
    }, [core, editorValues, selectedOperation, workflow]);

    const handleAddStep = useCallback(() => {
        if (!selectedOperation) return;

        setWorkflow((current) => current.concat([{
            id: makeId(),
            operation: selectedOperation,
            values: { ...editorValues },
            status: 'idle',
            result: null,
            error: null,
        }]));
    }, [editorValues, selectedOperation]);

    const handleDeleteStep = useCallback((index: number) => {
        setWorkflow((current) => current.filter((_, i) => i !== index));
    }, []);

    const handleEditStep = useCallback((step: WorkflowStep) => {
        selectOperation(step.operation);
        setEditorValues({ ...step.values });
    }, [selectOperation]);

    const initialLoadDoneRef = useRef(false);
    const handleReset = useCallback(() => {
        // 保留工作流步骤，但重置每个步骤的运行状态
        setWorkflow((current) => current.map((step) => ({
            ...step,
            status: 'idle' as WorkflowStepStatus,
            result: null,
            error: null,
        })));
        // 重置表单为当前操作的默认值
        if (selectedOperation) {
            setEditorValues(buildDefaultValues(selectedOperation));
        } else {
            setEditorValues({});
        }
        setPreviewResult(null);
        setPreviewError(null);
        setExpandedResults(new Set());
    }, [selectedOperation]);

    const runWorkflowStep = useCallback(async (step: WorkflowStep, index: number, stepResults: any[] = []) => {
        setWorkflow((current) => current.map((item, itemIndex) => (
            itemIndex === index ? { ...item, status: 'running', error: null } : item
        )));

        try {
            const result = await core.customOperations.execute(
                step.operation.id,
                prepareStepValues(step.values, stepResults, index),
            );
            setWorkflow((current) => current.map((item, itemIndex) => (
                itemIndex === index ? { ...item, status: 'success', result } : item
            )));
            return { ok: true, result };
        } catch (error) {
            const message = String(error);
            setWorkflow((current) => current.map((item, itemIndex) => (
                itemIndex === index ? { ...item, status: 'failed', error: message } : item
            )));
            return { ok: false, error: message };
        }
    }, [core]);

    const handleRunWorkflow = useCallback(async () => {
        if (!workflow.length) return;

        setRunning(true);
        try {
            const stepResults: any[] = [];
            for (let index = 0; index < workflow.length; index += 1) {
                const step = workflow[index];
                const outcome = await runWorkflowStep(step, index, stepResults);
                if (!outcome.ok) break;
                stepResults[index] = outcome.result;
            }
        } finally {
            setRunning(false);
        }
    }, [runWorkflowStep, workflow]);

    const handleCreateOperation = useCallback(async () => {
        if (!draft.name.trim() || !draft.nuclio_function.trim()) {
            notification.error({
                message: '名称和 Nuclio 函数名不能为空',
            });
            return;
        }

        if (draft.kind === 'model' && !draft.artifact) {
            notification.error({
                message: '模型操作需要上传模型文件',
            });
            return;
        }

        let inputSchema;
        let outputSchema;

        try {
            inputSchema = safeParseJSON(draft.input_schema, []);
            outputSchema = safeParseJSON(draft.output_schema, {});
        } catch (error) {
            notification.error({
                message: 'JSON 结构格式无效',
                description: String(error),
            });
            return;
        }

        try {
            await core.customOperations.create({
                name: draft.name,
                kind: draft.kind,
                description: draft.description,
                nuclio_function: draft.nuclio_function,
                input_schema: JSON.stringify(inputSchema),
                output_schema: JSON.stringify(outputSchema),
                is_active: draft.is_active,
                artifact: draft.artifact || undefined,
            });

            notification.success({
                message: '自定义操作已保存',
            });
            setDrawerVisible(false);
            setDraft({
                name: '',
                kind: 'model',
                description: '',
                nuclio_function: '',
                input_schema: '[]',
                output_schema: '{}',
                is_active: true,
                artifact: null,
            });
            await loadOperations();
        } catch (error) {
            notification.error({
                message: '无法保存自定义操作',
                description: String(error),
            });
        }
    }, [core, draft, loadOperations]);

    const modelTabs = [
        {
            key: 'model',
            label: '模型',
            children: (
                <div className='cvat-workflows-list'>
                    {models.length ? models.map((operation) => (
                        <button
                            key={operation.id}
                            type='button'
                            className={`cvat-workflow-operation-row ${selectedOperation?.id === operation.id ? 'cvat-workflow-operation-row-selected' : ''}`}
                            onClick={() => selectOperation(operation)}
                        >
                            <div className='cvat-workflow-operation-row-main'>
                                <Text strong>{operation.name}</Text>
                                <Tag color='blue'>{operation.nuclio_function}</Tag>
                            </div>
                            <Text type='secondary'>{operation.description || '暂无说明'}</Text>
                        </button>
                    )) : <Empty description='暂无自定义模型' />}
                </div>
            ),
        },
        {
            key: 'augmentation',
            label: '数据增强',
            children: (
                <div className='cvat-workflows-list'>
                    {augmentations.length ? augmentations.map((operation) => (
                        <button
                            key={operation.id}
                            type='button'
                            className={`cvat-workflow-operation-row ${selectedOperation?.id === operation.id ? 'cvat-workflow-operation-row-selected' : ''}`}
                            onClick={() => selectOperation(operation)}
                        >
                            <div className='cvat-workflow-operation-row-main'>
                                <Text strong>{operation.name}</Text>
                                <Tag color='green'>{operation.nuclio_function}</Tag>
                            </div>
                            <Text type='secondary'>{operation.description || '暂无说明'}</Text>
                        </button>
                    )) : <Empty description='暂无数据增强操作' />}
                </div>
            ),
        },
    ];

    return (
        <div className='cvat-workflows-page'>
            <div className='cvat-workflows-page-header'>
                <div>
                    <Text className='cvat-title'>工作流</Text>
                    <div>
                        <Text type='secondary'>选择已注册的模型和数据增强操作，按顺序组合并执行。</Text>
                    </div>
                </div>
                <Space>
                    <Button icon={<UndoOutlined />} onClick={handleReset}>
                        重置
                    </Button>
                    <Button icon={<ReloadOutlined />} onClick={() => { loadOperations(); }}>
                        刷新
                    </Button>
                    {allowManualRegistration ? (
                        <Button icon={<PlusOutlined />} type='primary' onClick={() => setDrawerVisible(true)}>
                            注册操作
                        </Button>
                    ) : null}
                </Space>
            </div>

            <div className='cvat-workflows-layout'>
                <div className='cvat-workflows-left' style={{ width: `${leftWidth}%`, height: '100%', flexShrink: 0 }}>
                    <div className='cvat-workflows-panel'>
                        <Tabs
                            activeKey={selectedKind}
                            onChange={(key) => {
                                const nextKind = key as CustomOperationKind;
                                if (nextKind !== selectedKind) {
                                    setSelectedKind(nextKind);
                                }
                            }}
                            items={modelTabs}
                        />
                    </div>
                </div>

                <div
                    ref={resizerRef}
                    className={`cvat-workflows-resizer ${isResizing ? 'cvat-workflows-resizer-active' : ''}`}
                    onMouseDown={handleResizerMouseDown}
                >
                    <div className='cvat-workflows-resizer-handle' />
                </div>

                <div className='cvat-workflows-right' style={{ flex: 1, minWidth: 0, height: '100%' }}>
                    <div className='cvat-workflows-panel cvat-workflow-builder-panel'>
                        {/* ---- 上半部分：配置区域 ---- */}
                        <div className='cvat-workflow-builder-top' style={{ height: `${topHeight}%`, flexShrink: 0 }}>
                            {selectedOperation ? (
                                <>
                                    <div className='cvat-workflow-builder-header'>
                                        <div>
                                            <Text strong className='cvat-workflow-builder-title'>
                                                {selectedOperation.name}
                                            </Text>
                                            <div className='cvat-workflow-builder-meta'>
                                                <Tag>{OPERATION_KIND_LABELS[selectedOperation.kind]}</Tag>
                                                <Tag color='blue'>{selectedOperation.nuclio_function}</Tag>
                                                {selectedOperation.artifact_url ? (
                                                    <a href={selectedOperation.artifact_url}
                                                        target='_blank' rel='noreferrer'>
                                                        {selectedOperation.artifact_name || '附件'}
                                                    </a>
                                                ) : null}
                                            </div>
                                        </div>
                                        <Space>
                                            <Button icon={<PlusOutlined />}
                                                onClick={handleAddStep}>
                                                添加步骤
                                            </Button>
                                            <Button
                                                icon={<PlayCircleOutlined />}
                                                type='primary'
                                                loading={running}
                                                onClick={() => { handleRunCurrent(); }}>
                                                运行当前步骤
                                            </Button>
                                        </Space>
                                    </div>

                                    {selectedOperation.description ? (
                                        <Text type='secondary'>{selectedOperation.description}</Text>
                                    ) : null}

                                    <Divider />

                                    <div className='cvat-workflow-builder-form'>
                                        {selectedOperation.input_schema.length ? (
                                            selectedOperation.input_schema.map((field) => (
                                                <OperationFieldEditor
                                                    key={field.name}
                                                    field={field}
                                                    value={editorValues[field.name]}
                                                    previousStepOptions={workflowStepOptions}
                                                    onChange={(nextValue) => setEditorValues((current) => ({
                                                        ...current,
                                                        [field.name]: nextValue,
                                                    }))}
                                                />
                                            ))
                                        ) : <Empty description='当前操作没有声明输入' />}

                                        <Divider />

                                        <div className='cvat-workflow-field'>
                                            <div className='cvat-workflow-field-label'>
                                                <Text strong>输出路径</Text>
                                            </div>
                                            <Input
                                                value={editorValues[OUTPUT_PATH_FIELD]}
                                                placeholder='留空则使用默认运行目录'
                                                onChange={(event) => setEditorValues((current) => ({
                                                    ...current,
                                                    [OUTPUT_PATH_FIELD]: event.target.value,
                                                }))}
                                            />
                                        </div>

                                        <div className='cvat-workflow-field'>
                                            <div className='cvat-workflow-field-label'>
                                                <Text strong>保存输出和运行记录</Text>
                                            </div>
                                            <Switch
                                                checked={editorValues[SAVE_OUTPUTS_FIELD] !== false}
                                                onChange={(checked) => setEditorValues((current) => ({
                                                    ...current,
                                                    [SAVE_OUTPUTS_FIELD]: checked,
                                                }))}
                                            />
                                        </div>
                                    </div>

                                    <div className='cvat-workflow-builder-footer'>
                                        <Space>
                                            <Button
                                                disabled={!workflow.length}
                                                icon={<PlayCircleOutlined />}
                                                loading={running}
                                                type='primary'
                                                onClick={() => { handleRunWorkflow(); }}>
                                                运行工作流
                                            </Button>
                                            <Button
                                                icon={<DeleteOutlined />}
                                                onClick={() => setWorkflow([])}>
                                                清空工作流
                                            </Button>
                                        </Space>
                                        <Button
                                            type='link'
                                            onClick={() => history.push('/models')}>
                                            打开内置模型
                                        </Button>
                                    </div>
                                    <Divider style={{ marginBottom: 0 }} />
                                </>
                            ) : (
                                <Empty description='请选择一个模型或数据增强操作' />
                            )}
                        </div>

                        {/* ---- 上下区域拖动条 ---- */}
                        <div
                            ref={topResizerRef}
                            className={`cvat-workflow-builder-resizer ${isTopResizing ? 'cvat-workflow-builder-resizer-active' : ''}`}
                            onMouseDown={handleTopResizerMouseDown}
                        >
                            <div className='cvat-workflow-builder-resizer-handle' />
                        </div>

                        {/* ---- 下半部分：输出展示区域（Tabs） ---- */}
                        <div className='cvat-workflow-builder-bottom'>
                            <Tabs
                                defaultActiveKey='workflow-steps'
                                items={[
                                    {
                                        key: 'workflow-steps',
                                        label: '工作流步骤',
                                        children: (
                                            <div className='cvat-workflow-step-list'>
                                                {workflow.length ? workflow.map((step, index) => (
                                                    <div
                                                        key={step.id}
                                                        role='button'
                                                        tabIndex={0}
                                                        className={[
                                                            'cvat-workflow-step',
                                                            step.status === 'running' ? 'cvat-workflow-step-running' : '',
                                                            step.status === 'success' ? 'cvat-workflow-step-success' : '',
                                                            step.status === 'failed' ? 'cvat-workflow-step-failed' : '',
                                                        ].join(' ')}
                                                        onClick={() => {
                                                            setSelectedStepIndex(index);
                                                            setStepDetailVisible(true);
                                                        }}
                                                        onKeyDown={(e) => {
                                                            if (e.key === 'Enter' || e.key === ' ') {
                                                                e.preventDefault();
                                                                setSelectedStepIndex(index);
                                                                setStepDetailVisible(true);
                                                            }
                                                        }}
                                                    >
                                                        <div className='cvat-workflow-step-header'>
                                                            <button
                                                                type='button'
                                                                className='cvat-workflow-step-name'
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    selectOperation(step.operation);
                                                                    setEditorValues(step.values);
                                                                }}
                                                            >
                                                                <Text strong>
                                                                    {`第 ${index + 1} 步：${step.operation.name}`}
                                                                </Text>
                                                            </button>
                                                            <Space>
                                                                <Tag>{STATUS_LABELS[step.status]}</Tag>
                                                                <Tooltip title='运行此步骤'>
                                                                    <Button
                                                                        size='small'
                                                                        icon={<PlayCircleOutlined />}
                                                                        loading={step.status === 'running'}
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            runWorkflowStep(
                                                                                step,
                                                                                index,
                                                                                workflow.map((item) => item.result),
                                                                            );
                                                                        }}
                                                                    />
                                                                </Tooltip>
                                                                <Tooltip title='编辑步骤'>
                                                                    <Button
                                                                        size='small'
                                                                        icon={<EditOutlined />}
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleEditStep(step);
                                                                        }}
                                                                    />
                                                                </Tooltip>
                                                                <Tooltip title='删除步骤'>
                                                                    <Button
                                                                        size='small'
                                                                        danger
                                                                        icon={<DeleteOutlined />}
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleDeleteStep(index);
                                                                        }}
                                                                    />
                                                                </Tooltip>
                                                            </Space>
                                                        </div>
                                                        <Text type='secondary'>
                                                            {step.operation.nuclio_function}
                                                        </Text>
                                                        {step.error ? (
                                                            <Text type='danger'>{step.error}</Text>
                                                        ) : null}
                                                        {step.result ? (
                                                            <div>
                                                                <div className='cvat-workflow-step-result-toggle'>
                                                                    <Button
                                                                        type='link'
                                                                        size='small'
                                                                        icon={
                                                                            expandedResults.has(step.id)
                                                                                ? <UpOutlined />
                                                                                : <DownOutlined />
                                                                        }
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            toggleResultExpand(step.id);
                                                                        }}
                                                                    >
                                                                        {expandedResults.has(step.id) ? '收起结果' : '展开结果'}
                                                                    </Button>
                                                                </div>
                                                                {expandedResults.has(step.id) ? (
                                                                    <pre className='cvat-workflow-step-result'>
                                                                        {prettyJSON(step.result)}
                                                                    </pre>
                                                                ) : (
                                                                    <div className='cvat-workflow-step-result-collapsed'>
                                                                        <Text type='secondary'>
                                                                            结果已折叠 · 点击"展开结果"查看
                                                                        </Text>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                )) : (
                                                    <Empty description='还没有添加工作流步骤' />
                                                )}
                                            </div>
                                        ),
                                    },
                                    {
                                        key: 'final-output',
                                        label: '最终输出结果',
                                        children: (
                                            <FinalOutputTabContent workflow={workflow} />
                                        ),
                                    },
                                ]}
                            />
                        </div>
                    </div>
                </div>
            </div>

            {/* ---- 步骤详情弹窗 ---- */}
            <Modal
                title={
                    selectedStepIndex !== null && workflow[selectedStepIndex]
                        ? `第 ${selectedStepIndex + 1} 步：${workflow[selectedStepIndex].operation.name}`
                        : '步骤详情'
                }
                open={stepDetailVisible}
                onCancel={() => {
                    setStepDetailVisible(false);
                    setSelectedStepIndex(null);
                }}
                footer={
                    <Button onClick={() => {
                        setStepDetailVisible(false);
                        setSelectedStepIndex(null);
                    }}>
                        关闭
                    </Button>
                }
                width={680}
                destroyOnClose
            >
                {selectedStepIndex !== null && workflow[selectedStepIndex] ? (() => {
                    const step = workflow[selectedStepIndex];
                    return (
                        <div className='cvat-workflow-step-detail'>
                            <Descriptions column={1} size='small' bordered>
                                <Descriptions.Item label='状态'>
                                    <Tag>
                                        {STATUS_LABELS[step.status]}
                                    </Tag>
                                </Descriptions.Item>
                                <Descriptions.Item label='操作类型'>
                                    <Tag color='blue'>
                                        {OPERATION_KIND_LABELS[step.operation.kind]}
                                    </Tag>
                                </Descriptions.Item>
                                <Descriptions.Item label='Nuclio 函数'>
                                    {step.operation.nuclio_function}
                                </Descriptions.Item>
                                <Descriptions.Item label='描述'>
                                    {step.operation.description || '暂无说明'}
                                </Descriptions.Item>
                            </Descriptions>

                            <Divider orientation='left'>输入参数</Divider>
                            <pre className='cvat-workflow-step-detail-json'>
                                {prettyJSON(step.values)}
                            </pre>

                            <Divider orientation='left'>输出结果</Divider>
                            {step.result ? (
                                <pre className='cvat-workflow-step-detail-json'>
                                    {prettyJSON(step.result)}
                                </pre>
                            ) : (
                                <Text type='secondary'>暂无输出</Text>
                            )}

                            {step.error ? (
                                <>
                                    <Divider orientation='left'>错误信息</Divider>
                                    <Text type='danger'>{step.error}</Text>
                                </>
                            ) : null}
                        </div>
                    );
                })() : (
                    <Empty description='无法加载步骤详情' />
                )}
            </Modal>

            <Drawer
                width={560}
                title='注册自定义操作'
                open={drawerVisible}
                onClose={() => setDrawerVisible(false)}
                destroyOnClose
            >
                <div className='cvat-workflow-drawer-form'>
                    <Text strong>名称</Text>
                    <Input
                        value={draft.name}
                        onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                    />

                    <Text strong>类型</Text>
                    <Select
                        value={draft.kind}
                        onChange={(value) => setDraft((current) => ({ ...current, kind: value }))}
                        options={[
                            { label: '模型', value: 'model' },
                            { label: '数据增强', value: 'augmentation' },
                        ]}
                    />

                    <Text strong>Nuclio 函数名</Text>
                    <Input
                        value={draft.nuclio_function}
                        onChange={(event) => setDraft((current) => ({
                            ...current,
                            nuclio_function: event.target.value,
                        }))}
                    />

                    <Text strong>说明</Text>
                    <Input.TextArea
                        value={draft.description}
                        autoSize={{ minRows: 3, maxRows: 6 }}
                        onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                    />

                    <Text strong>输入结构 JSON</Text>
                    <Input.TextArea
                        value={draft.input_schema}
                        autoSize={{ minRows: 6, maxRows: 14 }}
                        onChange={(event) => setDraft((current) => ({ ...current, input_schema: event.target.value }))}
                    />

                    <Text strong>输出结构 JSON</Text>
                    <Input.TextArea
                        value={draft.output_schema}
                        autoSize={{ minRows: 4, maxRows: 10 }}
                        onChange={(event) => setDraft((current) => ({ ...current, output_schema: event.target.value }))}
                    />

                    <Text strong>模型文件上传</Text>
                    <Upload
                        beforeUpload={() => false}
                        maxCount={1}
                        fileList={draft.artifact ? [{
                            uid: 'draft-artifact',
                            name: draft.artifact.name,
                            status: 'done' as const,
                        }] : []}
                        onChange={(info) => setDraft((current) => ({
                            ...current,
                            artifact: info.file.originFileObj || null,
                        }))}
                    >
                        <Button icon={<UploadOutlined />}>选择模型文件</Button>
                    </Upload>

                    <Text strong>启用</Text>
                    <Switch
                        checked={draft.is_active}
                        onChange={(checked) => setDraft((current) => ({ ...current, is_active: checked }))}
                    />

                    <Divider />

                    <Space>
                        <Button
                            type='primary'
                            onClick={() => { handleCreateOperation(); }}
                        >
                            保存
                        </Button>
                        <Button onClick={() => setDrawerVisible(false)}>取消</Button>
                    </Space>
                </div>
            </Drawer>

            {loading ? (
                <div className='cvat-workflows-loading'>
                    <Spin size='large' />
                </div>
            ) : null}
        </div>
    );
}
