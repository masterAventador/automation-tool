import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Typography,
} from "antd";
import { useRef, useState } from "react";

import { taskProjectionKeys } from "../../api/control-plane/task-projections";
import {
  MAX_TASK_TARGET_LIMIT,
  douyinActionMessageTemplateSchema,
  douyinSearchKeywordSchema,
} from "./task-creation-gateway";
import {
  TaskCreationGatewayError,
  type DouyinSearchExposureTaskDefinition,
  type TaskCreationGateway,
} from "./task-creation-gateway";

interface TaskCreateProps {
  readonly gateway: TaskCreationGateway;
  readonly onCreated: (taskId: string) => void;
}

interface TaskFormValues {
  searchKeyword: string;
  action: "browse" | "comment" | "direct_message";
  messageTemplate?: string;
  targetLimit: number;
  minimumIntervalSeconds: number;
  maximumIntervalSeconds: number;
}

const initialValues: TaskFormValues = {
  searchKeyword: "",
  action: "browse",
  targetLimit: 10,
  minimumIntervalSeconds: 30,
  maximumIntervalSeconds: 90,
};

export function TaskCreate({ gateway, onCreated }: TaskCreateProps) {
  const [form] = Form.useForm<TaskFormValues>();
  const action = Form.useWatch("action", form) ?? "browse";
  const queryClient = useQueryClient();
  const idempotencyKey = useRef<string | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (definition: DouyinSearchExposureTaskDefinition) => {
      idempotencyKey.current ??=
        `task:create:douyin-search:${globalThis.crypto.randomUUID()}`;
      return gateway.createDouyinSearchExposureTask(definition, idempotencyKey.current);
    },
    onSuccess: async (task) => {
      idempotencyKey.current = null;
      setCreatedTaskId(task.taskId);
      await queryClient.invalidateQueries({ queryKey: taskProjectionKeys.all });
    },
  });

  const credentialMissing =
    mutation.error instanceof TaskCreationGatewayError &&
    mutation.error.code === "credential_missing";

  const submit = (values: TaskFormValues) => {
    setCreatedTaskId(null);
    const definition: DouyinSearchExposureTaskDefinition = {
      template: "douyin.search_exposure.v1",
      searchKeyword: values.searchKeyword,
      action: values.action,
      messageTemplate: values.action === "browse" ? null : (values.messageTemplate ?? null),
      targetLimit: values.targetLimit,
      minimumIntervalSeconds: values.minimumIntervalSeconds,
      maximumIntervalSeconds: values.maximumIntervalSeconds,
      previewRequired: true,
      finalConfirmationRequired: true,
    };
    mutation.mutate(definition);
  };

  return (
    <Card className="task-create-card">
      <Space orientation="vertical" size={20} className="task-create-stack">
        <div>
          <Typography.Title level={3}>抖音搜索曝光任务</Typography.Title>
          <Typography.Text type="secondary">
            第一阶段只开放一个闭合模板，任务创建后再进入目标发现与确认流程。
          </Typography.Text>
        </div>

        <Flex gap={12} wrap>
          <Alert type="info" showIcon title="目标预览固定开启" />
          <Alert type="warning" showIcon title="执行前最终确认固定开启" />
        </Flex>

        <Form<TaskFormValues>
          form={form}
          layout="vertical"
          initialValues={initialValues}
          onFinish={submit}
          onValuesChange={() => {
            idempotencyKey.current = null;
            mutation.reset();
            setCreatedTaskId(null);
          }}
        >
          <Form.Item
            label="搜索关键词"
            name="searchKeyword"
            rules={[
              { required: true, message: "请输入搜索关键词" },
              {
                validator: async (_, value: string | undefined) => {
                  if (value === undefined || douyinSearchKeywordSchema.safeParse(value).success) {
                    return;
                  }
                  throw new Error("请输入有效的搜索关键词");
                },
              },
            ]}
          >
            <Input placeholder="例如：新能源汽车" autoComplete="off" />
          </Form.Item>

          <Form.Item label="动作" name="action" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "browse", label: "浏览" },
                { value: "comment", label: "评论" },
                { value: "direct_message", label: "私信" },
              ]}
            />
          </Form.Item>

          {action !== "browse" ? (
            <Form.Item
              label="评论或私信模板"
              name="messageTemplate"
              extra={"可用变量：{{target_display_name}}"}
              rules={[
                { required: true, message: "请输入评论或私信模板" },
                {
                  validator: async (_, value: string | undefined) => {
                    if (
                      value === undefined ||
                      douyinActionMessageTemplateSchema.safeParse(value).success
                    ) {
                      return;
                    }
                    throw new Error("请输入有效的评论或私信模板");
                  },
                },
              ]}
            >
              <Input.TextArea rows={4} autoComplete="off" />
            </Form.Item>
          ) : null}

          <Flex gap={16} wrap>
            <Form.Item
              label="单任务目标上限"
              name="targetLimit"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={MAX_TASK_TARGET_LIMIT} precision={0} />
            </Form.Item>
            <Form.Item
              label="最小间隔（秒）"
              name="minimumIntervalSeconds"
              dependencies={["maximumIntervalSeconds"]}
              rules={[
                { required: true },
                ({ getFieldValue }) => ({
                  validator(_, value: number) {
                    const maximum = getFieldValue("maximumIntervalSeconds") as number;
                    return value <= maximum
                      ? Promise.resolve()
                      : Promise.reject(new Error("最小间隔不能大于最大间隔"));
                  },
                }),
              ]}
            >
              <InputNumber min={1} max={3600} precision={0} />
            </Form.Item>
            <Form.Item
              label="最大间隔（秒）"
              name="maximumIntervalSeconds"
              dependencies={["minimumIntervalSeconds"]}
              rules={[{ required: true }]}
            >
              <InputNumber min={1} max={3600} precision={0} />
            </Form.Item>
          </Flex>

          {mutation.isError ? (
            <Alert
              className="task-create-notice"
              type="error"
              showIcon
              title={credentialMissing ? "当前设备尚未授权" : "任务创建失败"}
              description={
                credentialMissing
                  ? "请先完成本机安装授权后再创建任务。"
                  : "请检查业务服务连接后重试。"
              }
            />
          ) : null}
          {createdTaskId !== null ? (
            <Alert
              className="task-create-notice"
              type="success"
              showIcon
              title={`任务已创建：${createdTaskId}`}
              action={
                <Button size="small" onClick={() => onCreated(createdTaskId)}>
                  查看运行详情
                </Button>
              }
            />
          ) : null}

          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            创建任务
          </Button>
        </Form>
      </Space>
    </Card>
  );
}
