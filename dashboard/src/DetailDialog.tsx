import { type ReactNode, useEffect, useRef } from "react";

export function DetailDialog({ title, closeLabel, onClose, children }: {
  title: string;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (element.showModal) element.showModal();
    else element.setAttribute("open", "");
    return () => element.close?.();
  }, []);

  return <dialog ref={dialog} className="detail-dialog" aria-label={title} onCancel={onClose} onClose={onClose} onClick={(event) => { event.stopPropagation(); if (event.target === event.currentTarget) onClose(); }}>
    <header className="detail-dialog-header"><h2>{title}</h2><button type="button" className="dialog-close" aria-label={closeLabel} onClick={onClose}>×</button></header>
    {children}
  </dialog>;
}
