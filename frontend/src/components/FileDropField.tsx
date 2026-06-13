import { useId, useRef, useState, type DragEvent } from "react";
import type { UploadFieldInfo } from "../types/api";
import { formatFileSize } from "../lib/format";
import { Icon } from "./Icon";

function normalizedTypes(field: UploadFieldInfo): string[] {
  return field.file_types.map((t) => (t.startsWith(".") ? t.slice(1) : t).toLowerCase());
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/**
 * Labeled upload dropzone. Keyboard-operable (the browse button is a real button);
 * drag-drop is an enhancement, never the only path. Client-side validation is
 * extension-only - the file check does the real work.
 */
export function FileDropField({
  field,
  file,
  sampleActive,
  onSelect,
  compact = false,
}: {
  field: UploadFieldInfo;
  file: File | null;
  /** True when "Use sample data" is on - the slot shows the sample placeholder. */
  sampleActive: boolean;
  onSelect: (file: File | null) => void;
  compact?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const types = normalizedTypes(field);
  const accept = types.map((t) => `.${t}`).join(",");
  const typesText = types.join(", ");

  const trySelect = (candidate: File) => {
    if (!types.includes(extensionOf(candidate.name))) {
      setError(`This slot accepts ${typesText} files.`);
      return;
    }
    setError(null);
    onSelect(candidate);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) trySelect(dropped);
  };

  return (
    <div className={`upload-field${compact ? " upload-field-compact" : ""}`}>
      <span className="upload-label">
        {field.label}
        {field.required ? null : <span className="muted"> (optional)</span>}
      </span>
      <div
        className={`dropzone${dragOver ? " dropzone-over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {sampleActive ? (
          <p className="dropzone-sample">
            <Icon name="check-circle" size={16} /> Sample file will be used
          </p>
        ) : file ? (
          <div className="dropzone-selected">
            <span className="dropzone-file-name">
              {file.name} <span className="muted">({formatFileSize(file.size)})</span>
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                onSelect(null);
                setError(null);
                if (inputRef.current) inputRef.current.value = "";
              }}
            >
              Remove {file.name}
            </button>
          </div>
        ) : (
          <>
            <Icon name="upload" size={20} className="dropzone-icon" />
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => inputRef.current?.click()}
              aria-describedby={error ? errorId : undefined}
            >
              Choose file
            </button>
            <span className="dropzone-hint">or drag a file here</span>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            if (chosen) trySelect(chosen);
          }}
        />
      </div>
      <span className="upload-sublabel">Accepts: {typesText}</span>
      {field.help ? <span className="upload-help">{field.help}</span> : null}
      {error ? (
        <span id={errorId} className="field-error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
