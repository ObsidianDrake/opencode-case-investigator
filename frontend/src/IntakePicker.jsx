import { useEffect, useMemo, useRef, useState } from 'react';

export default function IntakePicker({ name, label, placeholder, names, value, onChange, disabled, error }) {
  const id = 'intake-' + name;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [active, setActive] = useState(-1);
  const list = useRef(null);
  const matches = useMemo(() => {
    if (!editing && names.includes(value)) return names;
    const query = value.trim().toLowerCase();
    return names.filter(name => name.toLowerCase().includes(query));
  }, [names, value, editing]);

  useEffect(() => {
    if (open && active >= 0) list.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  function close() {
    setOpen(false);
    setEditing(false);
    setActive(-1);
  }

  function choose(name) {
    onChange(name);
    close();
  }

  function keyDown(event) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      setActive(event.key === 'ArrowDown' ? Math.min(active + 1, matches.length - 1)
        : active < 0 ? matches.length - 1 : Math.max(active - 1, 0));
    } else if (open && event.key === 'Escape') {
      event.preventDefault();
      close();
    } else if (open && event.key === 'Enter') {
      event.preventDefault();
      const match = matches[active] ?? matches.find(name => name === value) ?? (matches.length === 1 && matches[0]);
      if (match) choose(match);
    }
  }

  return <div className="program-input">
    <label htmlFor={id}>{label} <small>必填</small></label>
    <input id={id} name={name} role="combobox" aria-autocomplete="list" autoComplete="off"
      aria-expanded={open} aria-controls={id + '-list'}
      aria-activedescendant={open && active >= 0 ? id + '-option-' + active : undefined}
      aria-invalid={error ? true : undefined} aria-describedby={error ? id + '-error' : undefined}
      placeholder={placeholder} value={value} required disabled={disabled}
      onFocus={() => setOpen(true)} onClick={() => setOpen(true)} onBlur={close} onKeyDown={keyDown}
      onChange={event => { onChange(event.target.value); setEditing(true); setActive(-1); setOpen(true); }} />
    <div hidden={!open} className="program-options">
      <ul id={id + '-list'} role="listbox" aria-label={label} ref={list}>
        {open && matches.map((name, i) => <li key={name} id={id + '-option-' + i} role="option"
          aria-selected={active === i} onMouseDown={event => event.preventDefault()}
          onClick={() => choose(name)}>{name}</li>)}
      </ul>
      {matches.length === 0 && <p role="status">沒有符合的選項</p>}
    </div>
    {error && <p id={id + '-error'} role="alert">{error}</p>}
  </div>;
}
