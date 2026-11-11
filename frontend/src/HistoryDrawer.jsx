import { useEffect, useState } from "react";
import { deleteMySession, fetchMySessions } from "./Auth.jsx";

// 历史列表只需要一个粗略的时间感：刚刚 / 分钟前 / 今天 / 日期。
function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  const now = new Date();
  const diff = now - date;
  if (diff < 60 * 1000) return "刚刚";
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (date.toDateString() === now.toDateString()) {
    return `今天 ${String(date.getHours()).padStart(2, "0")}:${String(
      date.getMinutes(),
    ).padStart(2, "0")}`;
  }
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

export default function HistoryDrawer({
  open,
  currentSessionId,
  onClose,
  onOpen,
  onDeleted,
}) {
  const [sessions, setSessions] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [confirmId, setConfirmId] = useState(null);

  // 每次打开都重新拉一次，保证刚结束的会话能立刻出现。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setStatus("loading");
    setError("");
    setConfirmId(null);
    fetchMySessions()
      .then((items) => {
        if (cancelled) return;
        setSessions(items);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function remove(sessionId) {
    // 第一下变成“确认删除”，第二下才真的删，避免误触。
    if (confirmId !== sessionId) {
      setConfirmId(sessionId);
      setError("");
      return;
    }
    try {
      await deleteMySession(sessionId);
      setSessions((items) => items.filter((item) => item.session_id !== sessionId));
      setConfirmId(null);
      onDeleted?.(sessionId);
    } catch (requestError) {
      setError(requestError.message || "删除失败，请稍后重试");
    }
  }

  return (
    <>
      <div className="history-scrim" onClick={onClose} aria-hidden="true" />
      <aside
        className="history-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="我的会话"
      >
        <header className="history-header">
          <div>
            <span className="section-kicker">HISTORY</span>
            <h2>我的会话</h2>
          </div>
          <button className="history-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>

        {status === "loading" && <p className="history-note">正在加载…</p>}
        {status === "error" && (
          <p className="history-note history-error">加载失败，请稍后再试。</p>
        )}
        {status === "ready" && sessions.length === 0 && (
          <p className="history-note">
            还没有会话记录。完成一次问诊后会自动出现在这里。
          </p>
        )}

        {status === "ready" && sessions.length > 0 && (
          <ul className="history-list">
            {sessions.map((item) => {
              const current = item.session_id === currentSessionId;
              return (
                <li
                  key={item.session_id}
                  className={"history-item" + (current ? " current" : "")}
                >
                  <button
                    className="history-open"
                    onClick={() => onOpen(item.session_id)}
                  >
                    <strong>{item.title || "新的问诊会话"}</strong>
                    <span>
                      {formatTime(item.last_active_at)}
                      {current ? " · 当前会话" : ""}
                    </span>
                  </button>
                  <button
                    className={
                      "history-delete" +
                      (confirmId === item.session_id ? " confirming" : "")
                    }
                    onClick={() => remove(item.session_id)}
                    aria-label={"删除会话 " + (item.title || "新的问诊会话")}
                  >
                    {confirmId === item.session_id ? "确认删除" : "删除"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {error && <p className="history-note history-error">{error}</p>}
      </aside>
    </>
  );
}
