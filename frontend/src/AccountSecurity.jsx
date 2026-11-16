import { useState } from "react";

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

export function verifyEmail(token) {
  return request("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function resendVerification() {
  return request("/auth/resend-verification", { method: "POST" });
}

export function forgotPassword(email) {
  return request("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token, newPassword) {
  return request("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

export function EmailVerifyPanel({ open, user, onClose, onVerified }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);

  if (!open) return null;

  async function handleVerify() {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await verifyEmail(token.trim());
      onVerified?.({ ...user, email_verified: true });
      onClose?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  async function handleResend() {
    setPending(true);
    setError("");
    setNotice("");
    try {
      const data = await resendVerification();
      setNotice(
        data.sent
          ? "验证码已重新发送。开发模式下请查看后端日志。"
          : "邮件发送失败，请检查 SMTP 配置或稍后重试。",
      );
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="privacy-scrim" onClick={onClose} aria-hidden="true" />
      <section className="privacy-panel" role="dialog" aria-modal="true">
        <header>
          <div>
            <span className="section-kicker">EMAIL</span>
            <h2>验证邮箱</h2>
          </div>
          <button className="history-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>
        <p className="privacy-version">
          当前邮箱：{user?.email}。输入邮件中的验证码完成验证。
        </p>
        <div className="auth-field">
          <label htmlFor="verify-token">验证码</label>
          <input
            id="verify-token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴邮件中的验证码"
            disabled={pending}
          />
        </div>
        {notice && <p className="privacy-notice">{notice}</p>}
        {error && <p className="privacy-error">{error}</p>}
        <div className="privacy-actions">
          <button className="primary-action" onClick={handleVerify} disabled={pending}>
            完成验证
          </button>
          <button className="text-button" onClick={handleResend} disabled={pending}>
            重新发送
          </button>
        </div>
        <p className="privacy-version">
          开发模式：验证码会写入后端日志（EMAIL_BACKEND=console）。
        </p>
      </section>
    </>
  );
}

export function ForgotPasswordPanel({ open, onClose, onReset }) {
  const [step, setStep] = useState("email");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);

  if (!open) return null;

  async function handleSend() {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await forgotPassword(email.trim());
      setStep("reset");
      setNotice("如果邮箱已注册，验证码已发送。开发模式下请查看后端日志。");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  async function handleReset() {
    if (password.length < 8 || !/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
      setError("密码至少 8 位，并同时包含字母和数字");
      return;
    }
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setPending(true);
    setError("");
    try {
      await resetPassword(token.trim(), password);
      onReset?.();
      onClose?.();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="privacy-scrim" onClick={onClose} aria-hidden="true" />
      <section className="privacy-panel" role="dialog" aria-modal="true">
        <header>
          <div>
            <span className="section-kicker">ACCOUNT</span>
            <h2>找回密码</h2>
          </div>
          <button className="history-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>

        {step === "email" ? (
          <>
            <p className="privacy-version">
              输入注册邮箱，我们会发送一次性验证码。无论邮箱是否存在，页面都会返回相同结果。
            </p>
            <div className="auth-field">
              <label htmlFor="forgot-email">邮箱</label>
              <input
                id="forgot-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                disabled={pending}
              />
            </div>
            {error && <p className="privacy-error">{error}</p>}
            <div className="privacy-actions">
              <button className="primary-action" onClick={handleSend} disabled={pending || !email}>
                发送验证码
              </button>
            </div>
          </>
        ) : (
          <>
            {notice && <p className="privacy-notice">{notice}</p>}
            <div className="auth-field">
              <label htmlFor="reset-token">验证码</label>
              <input
                id="reset-token"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="粘贴邮件中的验证码"
                disabled={pending}
              />
            </div>
            <div className="auth-field">
              <label htmlFor="reset-password">新密码</label>
              <input
                id="reset-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="至少 8 位，包含字母和数字"
                disabled={pending}
              />
            </div>
            <div className="auth-field">
              <label htmlFor="reset-confirm">确认新密码</label>
              <input
                id="reset-confirm"
                type="password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                placeholder="再输入一次新密码"
                disabled={pending}
              />
            </div>
            {error && <p className="privacy-error">{error}</p>}
            <div className="privacy-actions">
              <button className="primary-action" onClick={handleReset} disabled={pending}>
                重置密码
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
