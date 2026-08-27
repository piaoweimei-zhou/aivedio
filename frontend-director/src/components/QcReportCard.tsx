import React, { useEffect, useState } from 'react';
import { Card, Tag, Progress, Descriptions, Button, Space, message, Alert, Spin } from 'antd';

export interface QcDimension {
  name: string;
  score: number;          // 0-100
  weight?: number;
  detail?: string;
}

export interface QcReport {
  total_score: number;
  dimensions: QcDimension[];
  dimensions_list?: QcDimension[];
  redlines?: any[];
  copyright?: any;
  platform_rules?: any;
  summary?: string;
  passed?: boolean;
  blocked?: boolean;
  gate?: {
    passed: boolean;
    blocked: boolean;
    forced_publish?: boolean;
    note?: string;
    forced_by?: string;
    forced_reason?: string;
  };
}

const RADAR_SIZE = 240;
const CENTER = RADAR_SIZE / 2;
const RADIUS = RADAR_SIZE / 2 - 36;

function polarPoint(idx: number, total: number, ratio: number) {
  const angle = (Math.PI * 2 * idx) / total - Math.PI / 2;
  return {
    x: CENTER + Math.cos(angle) * RADIUS * ratio,
    y: CENTER + Math.sin(angle) * RADIUS * ratio,
  };
}

function RadarChart({ dims }: { dims: QcDimension[] }) {
  if (!dims.length) return null;
  const total = dims.length;
  const gridRings = [0.25, 0.5, 0.75, 1];
  const axisPts = dims.map((_, i) => polarPoint(i, total, 1));
  const valuePts = dims.map((d, i) => polarPoint(i, total, Math.max(0, Math.min(1, (d.score || 0) / 100))));

  const toPath = (pts: { x: number; y: number }[]) => pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

  return (
    <svg width={RADAR_SIZE} height={RADAR_SIZE} style={{ maxWidth: '100%' }}>
      {gridRings.map((r) => (
        <polygon
          key={r}
          points={toPath(dims.map((_, i) => polarPoint(i, total, r)))}
          fill="none"
          stroke="#e5e7eb"
          strokeWidth={1}
        />
      ))}
      {axisPts.map((p, i) => (
        <line key={i} x1={CENTER} y1={CENTER} x2={p.x} y2={p.y} stroke="#e5e7eb" strokeWidth={1} />
      ))}
      <polygon points={toPath(valuePts)} fill="rgba(24,144,255,0.25)" stroke="#1890ff" strokeWidth={2} />
      {valuePts.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={3} fill="#1890ff" />
      ))}
      {dims.map((d, i) => {
        const lp = polarPoint(i, total, 1.18);
        return (
          <text key={i} x={lp.x} y={lp.y} fontSize={11} textAnchor="middle" fill="#6b7280">
            {d.name}
          </text>
        );
      })}
    </svg>
  );
}

export default function QcReportCard({ assetId, forcePublish }: { assetId?: string; forcePublish?: (assetId: string, reason: string) => Promise<void> }) {
  const [report, setReport] = useState<QcReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forcing, setForcing] = useState(false);

  const load = async () => {
    if (!assetId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/qc/report/${assetId}`);
      const data = await res.json();
      if (data.success) setReport(data.report);
      else setError(data.error || '未找到质检报告');
    } catch (e: any) {
      setError(e?.message || '加载质检报告失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [assetId]);

  if (!assetId) return null;
  if (loading) return <Card size="small" title="成片质检"><Spin tip="质检中..." /></Card>;
  if (error) return <Card size="small" title="成片质检"><Alert type="info" showIcon message={error} /></Card>;
  if (!report) return null;

  const gate = report.gate || { passed: !!report.passed, blocked: !!report.blocked };
  const scoreColor = report.total_score >= 75 ? '#52c41a' : report.total_score >= 60 ? '#1890ff' : '#fa8c16';
  const canForce = gate.blocked && gate.forced_publish !== true && forcePublish;

  return (
    <Card
      size="small"
      title="成片质检报告"
      extra={
        <Space>
          <Tag color={scoreColor}>总分 {report.total_score?.toFixed?.(1) ?? report.total_score}</Tag>
          {gate.blocked ? <Tag color="red">红线拦截</Tag> : gate.passed ? <Tag color="green">通过</Tag> : <Tag color="orange">未达标</Tag>}
        </Space>
      }
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
        <RadarChart dims={report.dimensions_list || []} />
        <div style={{ flex: 1, minWidth: 260 }}>
          {(report.dimensions_list || []).map((d) => (
            <div key={d.name} style={{ marginBottom: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                <span>{d.name}</span>
                <span>{d.score?.toFixed?.(1) ?? d.score}</span>
              </div>
              <Progress percent={Math.round(d.score || 0)} size="small" strokeColor={d.score >= 75 ? '#52c41a' : d.score >= 60 ? '#1890ff' : '#fa8c16'} />
            </div>
          ))}
        </div>
      </div>

      {report.summary?.trim() ? (
        <Alert style={{ marginTop: 8 }} type="info" showIcon message="AI 总结" description={report.summary} />
      ) : (
        <Alert
          style={{ marginTop: 8 }}
          type="warning"
          showIcon
          message="AI 未生成文字总结"
          description="语义模型未返回文字结论，请以上方维度评分与雷达图为准。"
        />
      )}

      {gate.blocked && (
        <Alert
          style={{ marginTop: 8 }}
          type="error"
          showIcon
          message="红线拦截：存在版权或平台规则风险"
          description="该成片被质检门禁拦截，建议修改后重新生成；如确需发布可强制发布（将留痕）。"
          action={
            canForce ? (
              <Button
                danger
                size="small"
                loading={forcing}
                onClick={async () => {
                  setForcing(true);
                  try {
                    await forcePublish!(assetId, '导演确认强制发布');
                    message.success('已强制发布（已留痕）');
                    load();
                  } finally {
                    setForcing(false);
                  }
                }}
              >
                强制发布
              </Button>
            ) : gate.forced_publish ? (
              <Tag color="volcano">已强制发布 · {gate.forced_by || '未知'}</Tag>
            ) : null
          }
        />
      )}

      <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
        <Descriptions.Item label="门禁结果">
          {gate.forced_publish ? '已强制发布（留痕）' : gate.blocked ? '拦截' : gate.passed ? '通过' : '未达标（可发布）'}
        </Descriptions.Item>
        {gate.note && <Descriptions.Item label="说明">{gate.note}</Descriptions.Item>}
      </Descriptions>
    </Card>
  );
}
