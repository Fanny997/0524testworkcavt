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
import { Row, Col } from 'antd/lib/grid';
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
import {
    UploadOutlined, PlusOutlined, ReloadOutlined, PlayCircleOutlined, DeleteOutlined,
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
            const currentSelection = selectedOperationIdRef.current;
            const nextOperation = currentSelection ? list.find((operation) => operation.id === currentSelection) : null;
            const fallbackOperation = nextOperation || list[0] || null;
            if (fallbackOperation) {
                setSelectedOperationId(fallbackOperation.id);
                if (!nextOperation) {
                    setEditorValues(buildDefaultValues(fallbackOperation));
                }
            } else {
                setSelectedOperationId(null);
                setEditorValues({});
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

            <Row gutter={16} className='cvat-workflows-layout'>
                <Col xs={24} lg={9} xl={8} xxl={7}>
                    <div className='cvat-workflows-panel'>
                        <Tabs
                            activeKey={selectedKind}
                            onChange={(key) => {
                                const nextKind = key as CustomOperationKind;
                                setSelectedKind(nextKind);
                                const nextOperation = (nextKind === 'model' ? models : augmentations)[0] || null;
                                if (nextOperation) {
                                    selectOperation(nextOperation);
                                } else {
                                    setSelectedOperationId(null);
                                    setEditorValues({});
                                }
                            }}
                            items={modelTabs}
                        />
                    </div>
                </Col>

                <Col xs={24} lg={15} xl={16} xxl={17}>
                    <div className='cvat-workflows-panel cvat-workflow-builder-panel'>
                        {selectedOperation ? (
                            <>
                                <div className='cvat-workflow-builder-header'>
                                    <div>
                                        <Text strong className='cvat-workflow-builder-title'>{selectedOperation.name}</Text>
                                        <div className='cvat-workflow-builder-meta'>
                                            <Tag>{OPERATION_KIND_LABELS[selectedOperation.kind]}</Tag>
                                            <Tag color='blue'>{selectedOperation.nuclio_function}</Tag>
                                            {selectedOperation.artifact_url ? (
                                                <a href={selectedOperation.artifact_url} target='_blank' rel='noreferrer'>
                                                    {selectedOperation.artifact_name || '附件'}
                                                </a>
                                            ) : null}
                                        </div>
                                    </div>
                                    <Space>
                                        <Button
                                            icon={<PlusOutlined />}
                                            onClick={handleAddStep}
                                        >
                                            添加步骤
                                        </Button>
                                        <Button
                                            icon={<PlayCircleOutlined />}
                                            type='primary'
                                            loading={running}
                                            onClick={() => { handleRunCurrent(); }}
                                        >
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

                                <Divider />

                                <div className='cvat-workflow-builder-footer'>
                                    <Space>
                                        <Button
                                            disabled={!workflow.length}
                                            icon={<PlayCircleOutlined />}
                                            loading={running}
                                            type='primary'
                                            onClick={() => { handleRunWorkflow(); }}
                                        >
                                            运行工作流
                                        </Button>
                                        <Button
                                            icon={<DeleteOutlined />}
                                            onClick={() => setWorkflow([])}
                                        >
                                            清空工作流
                                        </Button>
                                    </Space>
                                    <Button
                                        type='link'
                                        onClick={() => history.push('/models')}
                                    >
                                        打开内置模型
                                    </Button>
                                </div>
                            </>
                        ) : (
                            <Empty description='请选择一个模型或数据增强操作' />
                        )}

                        <Divider />

                        <div className='cvat-workflow-step-list'>
                            {workflow.length ? workflow.map((step, index) => (
                                <div key={step.id} className={`cvat-workflow-step ${step.status === 'failed' ? 'cvat-workflow-step-failed' : ''}`}>
                                    <div className='cvat-workflow-step-header'>
                                        <button
                                            type='button'
                                            className='cvat-workflow-step-name'
                                            onClick={() => {
                                                selectOperation(step.operation);
                                                setEditorValues(step.values);
                                            }}
                                        >
                                            <Text strong>{`第 ${index + 1} 步：${step.operation.name}`}</Text>
                                        </button>
                                        <Space>
                                            <Tag>{STATUS_LABELS[step.status]}</Tag>
                                            <Tooltip title='运行此步骤'>
                                                <Button
                                                    size='small'
                                                    icon={<PlayCircleOutlined />}
                                                    loading={step.status === 'running'}
                                                    onClick={() => {
                                                        runWorkflowStep(
                                                            step,
                                                            index,
                                                            workflow.map((item) => item.result),
                                                        );
                                                    }}
                                                />
                                            </Tooltip>
                                        </Space>
                                    </div>
                                    <Text type='secondary'>{step.operation.nuclio_function}</Text>
                                    {step.error ? <Text type='danger'>{step.error}</Text> : null}
                                    {step.result ? (
                                        <pre className='cvat-workflow-step-result'>{prettyJSON(step.result)}</pre>
                                    ) : null}
                                </div>
                            )) : <Empty description='还没有添加工作流步骤' />}
                        </div>

                        {previewResult ? (
                            <>
                                <Divider />
                                <Text strong>最近一次结果</Text>
                                <pre className='cvat-workflow-step-result'>{prettyJSON(previewResult)}</pre>
                            </>
                        ) : null}

                        {previewError ? (
                            <>
                                <Divider />
                                <Text type='danger'>{previewError}</Text>
                            </>
                        ) : null}
                    </div>
                </Col>
            </Row>

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
