import { useEffect, useState } from "react";

export const PRIVACY_VERSION = "2026-11-16";

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "";
    const error = new Error(detail || "请求失败，请稍后重试");
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function recordConsent(version = PRIVACY_VERSION) {
  return request("/auth/consent", {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

export async function exportMyData() {
  const response = await fetch("/auth/export", { credentials: "same-origin" });
  if (!response.ok) throw new Error("导出失败，请稍后重试");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "consultation-export.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function deleteMyAccount(password) {
  return request("/auth/delete", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

const SECTIONS = [
  {
    title: "我们收集什么",
    text:
      "邮箱、昵称、登录状态；你输入的虚构病例、追问回答、结构化字段与最终结果；反馈评分和必要的技术日志。",
  },
  {
    title: "用来做什么",
    text:
      "提供问诊演示、保存会话历史、改进追问与安全策略。不会用于广告，也不会用于真实医疗诊断。",
  },
  {
    title: "保留多久",
    text:
      "默认保留 90 天，可通过 DATA_RETENTION_DAYS 调整；超过保留期的会话会被清理。你可以随时导出或删除自己的数据。",
  },
  {
    title: "第三方服务",
    text:
      "回答需要调用模型服务；部分外部检索工具由第三方托管。发送内容仅限完成该次回答所需的最少文本。",
  },
  {
    title: "你的权利",
    text:
      "可以导出账号与问诊数据；可以删除账号，删除后该账号的会话、反馈和登录状态不可恢复。",
  },
  {
    title: "重要边界",
    text:
      "本项目只处理虚构病例，不提供真实诊断、处方、剂量或停药建议。如有真实健康问题，请咨询专业人员。",
  },
];

export function PrivacyContent() {
  return (
    <div className="privacy-sections">
      {SECTIONS.map((section) => (
        <section key={section.title}>
          <h3>{section.title}</h3>
          <p>{section.text}</p>
        </section>
      ))}
    </div>
  );
}

export default function PrivacyPanel({
  open,
  user,
  onClose,
  onConsented,
  onDeleted,
}) {
  const [agreed, setAgreed] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const requiresConsent = Boolean(user?.consent_required);

  useEffect(() => {
    if (!open) return;
    setError("");
    setNotice("");
    setPassword("");
    setAgreed(false);
    const onKey = (event) => {
      if (event.key === "Escape" && !requiresConsent) onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, requiresConsent]);

  if (!open) return null;

  async function handleConsent() {
    setPending(true);
    setError("");
    try {
      const updated = await recordConsent();
      onConsented?.(updated);
      onClose?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  async function handleExport() {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await exportMyData();
      setNotice("导出已开始下载。");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  async function handleDelete() {
    if (!password) {
      setError("请输入密码确认删除");
      return;
    }
    setPending(true);
    setError("");
    try {
      await deleteMyAccount(password);
      onDeleted?.();
      onClose?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div
        className="privacy-scrim"
        onClick={requiresConsent ? undefined : onClose}
        aria-hidden="true"
      />
      <section
        className="privacy-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="privacy-title"
      >
        <header>
          <div>
            <span className="section-kicker">PRIVACY</span>
            <h2 id="privacy-title">隐私说明与数据使用</h2>
          </div>
          {!requiresConsent && (
            <button className="history-close" onClick={onClose} aria-label="关闭">
              ✕
            </button>
          )}
        </header>
        <p className="privacy-version">版本 {PRIVACY_VERSION}</p>
        <PrivacyContent />

        {requiresConsent && (
          <label className="privacy-consent">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
              disabled={pending}
            />
            <span>我已阅读并同意以上隐私说明</span>
          </label>
        )}

        {notice && <p className="privacy-notice">{notice}</p>}
        {error && <p className="privacy-error">{error}</p>}

        <div className="privacy-actions">
          {requiresConsent && (
            <button
              className="primary-action"
              onClick={handleConsent}
              disabled={!agreed || pending}
            >
              同意并继续
            </button>
          )}
          {!requiresConsent && user && (
            <>
              <button className="text-button" onClick={handleExport} disabled={pending}>
                导出我的数据
              </button>
              <div className="privacy-delete">
                <input
                  type="password"
                  placeholder="输入密码确认删除账号"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={pending}
                />
                <button className="danger-action" onClick={handleDelete} disabled={pending}>
                  删除账号
                </button>
              </div>
            </>
          )}
        </div>
      </section>
    </>
  );
}
