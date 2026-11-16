import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { deleteMySession, fetchMySessions, updateMySession } from "./Auth.jsx";

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

function sortByRecent(items) {
  return [...items].sort(
    (a, b) => new Date(b.last_active_at) - new Date(a.last_active_at),
  );
}

function sortByTitle(items) {
  return [...items].sort((a, b) =>
    (a.title || "").localeCompare(b.title || "", "zh-CN"),
  );
}

export default function HistoryDrawer({
  open,
  currentSessionId,
  onClose,
  onOpen,
  onDeleted,
  inline = false,
  refreshToken = 0,
}) {
  const [sessions, setSessions] = useState([]);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [menuId, setMenuId] = useState(null);
  const [menuPos, setMenuPos] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [pinnedCollapsed, setPinnedCollapsed] = useState(false);
  const [othersCollapsed, setOthersCollapsed] = useState(false);
  const [sortMode, setSortMode] = useState("recent");

  useEffect(() => {
    if (!open) return;
    setMenuId(null);
    setRenamingId(null);
    setPendingDelete(null);
    setPinnedCollapsed(false);
    setOthersCollapsed(false);
    setSortMode("recent");
  }, [open]);

  // 打开时和每轮对话后刷新；已有列表时不在切换期间闪“加载中”。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setStatus((current) => (current === "ready" ? current : "loading"));
    setError("");
    fetchMySessions(11)
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
  }, [open, refreshToken]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (pendingDelete) {
        if (!deleting) setPendingDelete(null);
        return;
      }
      if (menuId) {
        setMenuId(null);
        return;
      }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, menuId, pendingDelete, deleting]);

  useEffect(() => {
    if (!menuId) return;
    const closeMenu = () => setMenuId(null);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu);
    return () => {
      window.removeEventListener("scroll", closeMenu, true);
      window.removeEventListener("resize", closeMenu);
    };
  }, [menuId]);

  if (!open) return null;

  async function confirmDelete() {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    setError("");
    try {
      await deleteMySession(pendingDelete.session_id);
      setSessions((items) =>
        items.filter((item) => item.session_id !== pendingDelete.session_id),
      );
      onDeleted?.(pendingDelete.session_id);
      setPendingDelete(null);
    } catch (requestError) {
      setError(requestError.message || "删除失败，请稍后重试");
    } finally {
      setDeleting(false);
    }
  }

  function openMenu(event, item) {
    const rect = event.currentTarget.getBoundingClientRect();
    const width = 168;
    const height = 148;
    const left = Math.min(
      window.innerWidth - width - 8,
      Math.max(8, rect.right - width),
    );
    const top =
      rect.bottom + height > window.innerHeight
        ? Math.max(8, rect.top - height - 6)
        : rect.bottom + 6;
    setMenuId(item.session_id);
    setMenuPos({ left, top });
  }

  async function togglePin(item) {
    try {
      const updated = await updateMySession(item.session_id, {
        pinned: !item.pinned,
      });
      setSessions((items) =>
        items.map((entry) =>
          entry.session_id === item.session_id ? { ...entry, ...updated } : entry,
        ),
      );
      setMenuId(null);
    } catch (requestError) {
      setError(requestError.message || "置顶失败，请稍后重试");
    }
  }

  function startRename(item) {
    setRenamingId(item.session_id);
    setRenameValue(item.title || "");
    setMenuId(null);
  }

  async function saveRename(item) {
    const title = renameValue.trim();
    if (!title) {
      setError("标题不能为空");
      return;
    }
    try {
      const updated = await updateMySession(item.session_id, { title });
      setSessions((items) =>
        items.map((entry) =>
          entry.session_id === item.session_id ? { ...entry, ...updated } : entry,
        ),
      );
      setRenamingId(null);
      setError("");
    } catch (requestError) {
      setError(requestError.message || "重命名失败，请稍后重试");
    }
  }

  function renderItem(item) {
    const current = item.session_id === currentSessionId;
    return (
      <li
        key={item.session_id}
        className={
          "history-item" + (current ? " current" : "") + (item.pinned ? " pinned" : "")
        }
      >
        {renamingId === item.session_id ? (
          <form
            className="history-rename"
            onSubmit={(event) => {
              event.preventDefault();
              saveRename(item);
            }}
          >
            <input
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setRenamingId(null);
              }}
              aria-label="会话标题"
              maxLength={80}
              autoFocus
            />
            <button type="submit" aria-label="保存标题">
              ✓
            </button>
          </form>
        ) : (
          <>
            <button className="history-open" onClick={() => onOpen(item.session_id)}>
              <span className="history-bullet" aria-hidden="true" />
              <strong className="history-title">
                <span className="history-title-text">
                  {item.title || "新的问诊会话"}
                </span>
              </strong>
              <span className="history-time">{formatTime(item.last_active_at)}</span>
            </button>
            <button
              type="button"
              className="history-more"
              onClick={(event) => openMenu(event, item)}
              aria-label={"更多操作 " + (item.title || "新的问诊会话")}
              aria-haspopup="menu"
              aria-expanded={menuId === item.session_id}
            >
              ···
            </button>
          </>
        )}
      </li>
    );
  }

  function renderGroup({ label, items, collapsed, onToggle, showSort = false }) {
    if (items.length === 0) return null;
    const sorted = sortMode === "title" ? sortByTitle(items) : sortByRecent(items);
    return (
      <div className="history-group">
        <div className="history-group-head">
          <button
            type="button"
            className="history-group-toggle"
            onClick={onToggle}
            aria-expanded={!collapsed}
          >
            <span>{label}</span>
            <span
              className={"history-group-caret" + (collapsed ? "" : " open")}
              aria-hidden="true"
            >
              ⌄
            </span>
          </button>
          {showSort && (
            <button
              type="button"
              className="history-sort"
              onClick={() =>
                setSortMode((mode) => (mode === "recent" ? "title" : "recent"))
              }
              aria-label={sortMode === "recent" ? "按标题排序" : "按最近活跃排序"}
              title={sortMode === "recent" ? "按标题排序" : "按最近活跃排序"}
            >
              ⇅
            </button>
          )}
        </div>
        {!collapsed && (
          <ul className="history-list">{sorted.map((item) => renderItem(item))}</ul>
        )}
      </div>
    );
  }

  const menuItem = sessions.find((item) => item.session_id === menuId);
  const visibleSessions = sessions.slice(0, 10);
  const pinnedItems = visibleSessions.filter((item) => item.pinned);
  const otherItems = visibleSessions.filter((item) => !item.pinned);
  const hasMore = sessions.length > 10;

  const menu = menuId && menuPos && menuItem && (
    <>
      <div
        className="history-menu-scrim"
        onClick={() => setMenuId(null)}
        aria-hidden="true"
      />
      <div
        className="history-menu"
        style={{ left: menuPos.left, top: menuPos.top }}
        role="menu"
      >
        <button type="button" role="menuitem" onClick={() => togglePin(menuItem)}>
          {menuItem.pinned ? "取消置顶" : "置顶"}
        </button>
        <button type="button" role="menuitem" onClick={() => startRename(menuItem)}>
          重命名
        </button>
        <button
          type="button"
          role="menuitem"
          className="history-menu-danger"
          onClick={() => {
            setPendingDelete(menuItem);
            setError("");
            setMenuId(null);
          }}
        >
          删除
        </button>
      </div>
    </>
  );

  const confirmDialog = pendingDelete
    ? createPortal(
        <>
          <div
            className="confirm-scrim"
            onClick={() => {
              if (!deleting) {
                setPendingDelete(null);
                setError("");
              }
            }}
            aria-hidden="true"
          />
          <div
            className="confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-delete-title"
          >
            <h2 id="confirm-delete-title">删除这个会话？</h2>
            <p>
              「{pendingDelete.title || "新的问诊会话"}
              」及其对话记录将被删除，无法恢复。
            </p>
            {error && <p className="confirm-error">{error}</p>}
            <div className="confirm-actions">
              <button
                type="button"
                className="confirm-cancel"
                onClick={() => {
                  setPendingDelete(null);
                  setError("");
                }}
                disabled={deleting}
                autoFocus
              >
                取消
              </button>
              <button
                type="button"
                className="confirm-delete"
                onClick={confirmDelete}
                disabled={deleting}
              >
                {deleting ? "删除中…" : "删除"}
              </button>
            </div>
          </div>
        </>,
        document.body,
      )
    : null;

  const body = (
    <>
      {status === "loading" && <p className="history-note">正在加载…</p>}
      {status === "error" && (
        <p className="history-note history-error">加载失败，请稍后再试。</p>
      )}
      {status === "ready" && sessions.length === 0 && (
        <p className="history-note">还没有会话记录。完成一次问诊后会自动出现在这里。</p>
      )}

      {status === "ready" && sessions.length > 0 && (
        <>
          {renderGroup({
            label: "已置顶",
            items: pinnedItems,
            collapsed: pinnedCollapsed,
            onToggle: () => setPinnedCollapsed((value) => !value),
          })}
          {renderGroup({
            label: "对话和任务",
            items: otherItems,
            collapsed: othersCollapsed,
            onToggle: () => setOthersCollapsed((value) => !value),
            showSort: true,
          })}
          {hasMore && <p className="history-limit">仅显示最近 10 条</p>}
        </>
      )}

      {error && !pendingDelete && <p className="history-note history-error">{error}</p>}
    </>
  );

  if (inline) {
    return (
      <section className="sidebar-history" aria-label="历史会话">
        {body}
        {menu}
        {confirmDialog}
      </section>
    );
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
        {body}
        {menu}
        {confirmDialog}
      </aside>
    </>
  );
}
