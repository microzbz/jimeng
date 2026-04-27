import { useState, useRef, useEffect, useCallback } from 'react'
import { ReferenceAsset } from '@/services/api'

interface MentionInputProps {
    value: string
    onChange: (value: string) => void
    assets: ReferenceAsset[]
    placeholder?: string
    className?: string
}

export default function MentionInput({ value, onChange, assets, placeholder, className }: MentionInputProps) {
    const [showDropdown, setShowDropdown] = useState(false)
    const [query, setQuery] = useState('')
    const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0 })
    const [selectedIdx, setSelectedIdx] = useState(0)
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const mentionStart = useRef<number>(-1)

    const filtered = assets.filter(a => {
        const q = query.toLowerCase()
        if (a.name.toLowerCase().includes(q)) return true
        if (a.alias) {
            return a.alias.split(',').some(al => al.trim().toLowerCase().includes(q))
        }
        return false
    }).slice(0, 8)

    const handleInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const val = e.target.value
        const pos = e.target.selectionStart || 0
        onChange(val)

        const before = val.slice(0, pos)
        const atIdx = before.lastIndexOf('@')
        if (atIdx >= 0 && (atIdx === 0 || before[atIdx - 1] === ' ' || before[atIdx - 1] === '\n')) {
            const q = before.slice(atIdx + 1)
            if (!q.includes(' ') && !q.includes('\n')) {
                mentionStart.current = atIdx
                setQuery(q)
                setSelectedIdx(0)
                setShowDropdown(true)
                updateDropdownPosition(e.target, atIdx)
                return
            }
        }
        setShowDropdown(false)
    }, [onChange])

    const updateDropdownPosition = (textarea: HTMLTextAreaElement, _atIdx: number) => {
        const rect = textarea.getBoundingClientRect()
        setDropdownPos({
            top: rect.height + 4,
            left: 8,
        })
    }

    const selectAsset = useCallback((asset: ReferenceAsset) => {
        const start = mentionStart.current
        if (start < 0) return
        const before = value.slice(0, start)
        const cursorPos = textareaRef.current?.selectionStart || value.length
        const after = value.slice(cursorPos)
        const newVal = `${before}@${asset.name} ${after}`
        onChange(newVal)
        setShowDropdown(false)
        setTimeout(() => {
            const newPos = start + asset.name.length + 2
            textareaRef.current?.setSelectionRange(newPos, newPos)
            textareaRef.current?.focus()
        }, 0)
    }, [value, onChange])

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (!showDropdown || filtered.length === 0) return
        if (e.key === 'ArrowDown') {
            e.preventDefault()
            setSelectedIdx(i => (i + 1) % filtered.length)
        } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setSelectedIdx(i => (i - 1 + filtered.length) % filtered.length)
        } else if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault()
            selectAsset(filtered[selectedIdx])
        } else if (e.key === 'Escape') {
            setShowDropdown(false)
        }
    }, [showDropdown, filtered, selectedIdx, selectAsset])

    useEffect(() => {
        const handleClick = () => setShowDropdown(false)
        document.addEventListener('click', handleClick)
        return () => document.removeEventListener('click', handleClick)
    }, [])

    return (
        <div className="relative w-full">
            <textarea
                ref={textareaRef}
                value={value}
                onChange={handleInput}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                className={className}
                rows={2}
            />
            {showDropdown && filtered.length > 0 && (
                <div
                    className="absolute z-50 w-64 max-h-48 overflow-y-auto rounded-lg border border-white/10 bg-zinc-900/95 backdrop-blur-xl shadow-2xl"
                    style={{ top: dropdownPos.top, left: dropdownPos.left }}
                    onClick={e => e.stopPropagation()}
                >
                    {filtered.map((asset, idx) => (
                        <button
                            key={asset.id}
                            className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 transition-colors ${
                                idx === selectedIdx ? 'bg-white/10 text-white' : 'text-zinc-300 hover:bg-white/5'
                            }`}
                            onMouseDown={e => { e.preventDefault(); selectAsset(asset) }}
                            onMouseEnter={() => setSelectedIdx(idx)}
                        >
                            {asset.thumbnail_path ? (
                                <img src={`/${asset.thumbnail_path}`} className="w-6 h-6 rounded object-cover" alt="" />
                            ) : (
                                <div className="w-6 h-6 rounded bg-zinc-700 flex items-center justify-center text-xs">
                                    {asset.asset_type === 'video' ? '🎬' : '🖼'}
                                </div>
                            )}
                            <span className="truncate">{asset.name}</span>
                            {asset.alias && <span className="text-zinc-500 text-xs truncate">({asset.alias})</span>}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
