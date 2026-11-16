import { useRef, useState } from "react";
import { ForgotPasswordPanel } from "./AccountSecurity.jsx";
import PrivacyPanel, { PRIVACY_VERSION } from "./Privacy.jsx";

// 与后端保持一致的轻量校验；后端仍会再校验一次，前端只负责即时提示。
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PASSWORD_MIN = 8;

async function authRequest(path, options = {}) {
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

// 未登录不是错误，返回 null；网络错误交给调用方决定怎么提示。
export async function fetchCurrentUser() {
  try {
    return await authRequest("/auth/me");
  } catch (error) {
    if (error.status === 401) return null;
    throw error;
  }
}

export async function logoutUser() {
  try {
    await authRequest("/auth/logout", { method: "POST" });
  } catch {
    /* 网络失败也允许本地退出，Cookie 到期后自然失效 */
  }
}

// 登录后把本机上的游客会话归到账号；失败不阻塞页面。
export async function claimSession(sessionId) {
  return authRequest("/me/sessions/claim", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function fetchMySessions() {
  return authRequest("/me/sessions");
}

export async function deleteMySession(sessionId) {
  return authRequest("/me/sessions/" + encodeURIComponent(sessionId), {
    method: "DELETE",
  });
}

function validate({ mode, email, password, confirm, displayName, consent }) {
  if (!EMAIL_PATTERN.test(email.trim())) return "请输入有效的邮箱地址";
  if (!password) return "请输入密码";
  if (mode === "register") {
    if (password.length < PASSWORD_MIN) return `密码至少 ${PASSWORD_MIN} 位`;
    if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
      return "密码需要同时包含字母和数字";
    }
    if (password !== confirm) return "两次输入的密码不一致";
    if (displayName.trim().length > 30) return "昵称不能超过 30 字";
    if (!consent) return "请先阅读并同意隐私说明";
  }
  return "";
}

export default function AuthScreen({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [consent, setConsent] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);
  const [resetNotice, setResetNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const emailRef = useRef(null);

  const isRegister = mode === "register";

  function switchMode(next) {
    if (pending || next === mode) return;
    setMode(next);
    setError("");
    setPassword("");
    setConfirm("");
    setShowPassword(false);
    // 切换后把焦点放回邮箱，键盘用户不用再找一遍。
    requestAnimationFrame(() => emailRef.current?.focus());
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (pending) return;

    const problem = validate({ mode, email, password, confirm, displayName, consent });
    if (problem) {
      setError(problem);
      return;
    }

    setPending(true);
    setError("");
    try {
      const data = await authRequest(isRegister ? "/auth/register" : "/auth/login", {
        method: "POST",
        body: JSON.stringify(
          isRegister
            ? {
                email: email.trim(),
                password,
                display_name: displayName.trim(),
                consent: true,
                consent_version: PRIVACY_VERSION,
              }
            : { email: email.trim(), password },
        ),
      });
      onAuthed(data.user);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="auth-shell">
      <aside className="auth-aside">
        <div className="brand auth-brand">
          <span className="brand-mark">✳</span>
          <span>
            Consult<span className="brand-light">/Lab</span>
          </span>
        </div>

        <div className="auth-aside-copy">
          <div className="eyebrow auth-eyebrow">
            <span className="eyebrow-dot" /> ACCOUNT ACCESS
          </div>
          <h1>
            先确认身份，
            <br />
            <span>再开始问诊。</span>
          </h1>
          <p>
            登录后即可进入模拟问诊工作台。账号只用于保存你的使用状态，
            不会关联任何真实患者信息。
          </p>
        </div>

        <ul className="auth-points">
          <li>
            <span className="auth-point-index">01</span>
            <div>
              <strong>会话隔离</strong>
              <p>每次演示独立成轮，随时可以重新开始。</p>
            </div>
          </li>
          <li>
            <span className="auth-point-index">02</span>
            <div>
              <strong>安全登录</strong>
              <p>密码加盐哈希，登录令牌只存放在 HttpOnly Cookie 中。</p>
            </div>
          </li>
          <li>
            <span className="auth-point-index">03</span>
            <div>
              <strong>完整轨迹</strong>
              <p>追问、风险分流与反馈都留有可回看的记录。</p>
            </div>
          </li>
        </ul>

        <div className="notice auth-notice">
          <span aria-hidden="true">ⓘ</span>
          <span>仅限虚构病例演示，不提供真实医疗诊断、治疗或用药指导。</span>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-card">
          <div className="auth-tabs" role="tablist" aria-label="登录或注册">
            <button
              type="button"
              role="tab"
              aria-selected={!isRegister}
              className={!isRegister ? "active" : ""}
              onClick={() => switchMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={isRegister}
              className={isRegister ? "active" : ""}
              onClick={() => switchMode("register")}
            >
              注册
            </button>
          </div>

          <h2>{isRegister ? "创建你的账号" : "欢迎回来"}</h2>
          <p className="auth-sub">
            {isRegister
              ? "注册后自动登录，随时可以退出。"
              : "使用注册时的邮箱和密码进入工作台。"}
          </p>

          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            {isRegister && (
              <div className="auth-field">
                <label htmlFor="auth-name">
                  昵称 <span className="auth-optional">选填</span>
                </label>
                <input
                  id="auth-name"
                  type="text"
                  value={displayName}
                  maxLength={30}
                  autoComplete="nickname"
                  placeholder="你希望怎么被称呼"
                  onChange={(event) => setDisplayName(event.target.value)}
                  disabled={pending}
                />
              </div>
            )}

            <div className="auth-field">
              <label htmlFor="auth-email">邮箱</label>
              <input
                id="auth-email"
                ref={emailRef}
                type="email"
                value={email}
                autoComplete="email"
                placeholder="you@example.com"
                onChange={(event) => setEmail(event.target.value)}
                disabled={pending}
                required
              />
            </div>

            <div className="auth-field">
              <label htmlFor="auth-password">密码</label>
              <div className="auth-input">
                <input
                  id="auth-password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  autoComplete={isRegister ? "new-password" : "current-password"}
                  placeholder={isRegister ? "至少 8 位，包含字母和数字" : "输入密码"}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={pending}
                  required
                />
                <button
                  type="button"
                  className="auth-reveal"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  tabIndex={-1}
                >
                  {showPassword ? "隐藏" : "显示"}
                </button>
              </div>
            </div>

            {isRegister && (
              <div className="auth-field">
                <label htmlFor="auth-confirm">确认密码</label>
                <input
                  id="auth-confirm"
                  type={showPassword ? "text" : "password"}
                  value={confirm}
                  autoComplete="new-password"
                  placeholder="再输入一次密码"
                  onChange={(event) => setConfirm(event.target.value)}
                  disabled={pending}
                  required
                />
              </div>
            )}

            {isRegister && (
              <label className="auth-consent">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(event) => setConsent(event.target.checked)}
                  disabled={pending}
                />
                <span>
                  我已阅读并同意
                  <button
                    type="button"
                    className="auth-link"
                    onClick={() => setPrivacyOpen(true)}
                  >
                    《隐私说明》
                  </button>
                </span>
              </label>
            )}

            {!isRegister && (
              <button
                type="button"
                className="auth-link auth-forgot"
                onClick={() => setForgotOpen(true)}
              >
                忘记密码？
              </button>
            )}

            {resetNotice && (
              <p className="auth-notice-inline">{resetNotice}</p>
            )}

            {error && (
              <div className="auth-error" role="alert">
                {error}
              </div>
            )}

            <button className="auth-submit" type="submit" disabled={pending}>
              {pending ? "请稍候…" : isRegister ? "注册并进入" : "登录"}
              {!pending && <span aria-hidden="true">↗</span>}
            </button>
          </form>

          <p className="auth-switch">
            {isRegister ? "已经有账号了？" : "还没有账号？"}
            <button
              type="button"
              className="auth-link"
              onClick={() => switchMode(isRegister ? "login" : "register")}
              disabled={pending}
            >
              {isRegister ? "直接登录" : "创建一个"}
            </button>
          </p>

          <div className="auth-divider" aria-hidden="true" />
          <p className="auth-footnote">
            登录状态保持 7 天。在同一浏览器再次打开时会自动恢复。
          </p>
        </div>

        <p className="auth-footer">
          CONSULT / LAB <span>·</span> 虚构病例交互演示
          <span className="build-tag">BUILD {__BUILD_ID__}</span>
        </p>
      </main>

      <PrivacyPanel
        open={privacyOpen}
        user={null}
        onClose={() => setPrivacyOpen(false)}
      />
      <ForgotPasswordPanel
        open={forgotOpen}
        onClose={() => setForgotOpen(false)}
        onReset={() => {
          setMode("login");
          setPassword("");
          setConfirm("");
          setError("");
          setResetNotice("密码已重置，请用新密码登录。");
        }}
      />
    </div>
  );
}
