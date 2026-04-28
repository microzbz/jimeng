import { useState, useRef, useEffect } from 'react'
import { ReferenceAsset } from '@/services/api'
import { cn } from '@/lib/utils'

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
    const [selectedIndex, setSelectedIndex] = useState(0)
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const dropdownRef = useRef<HTMLDivElement>(null)
    const mentionStartRef = useRef<number>(-1)

    const filtered = assets.filter(a => {
        const q = query.toLowerCase()
        return a.name.toLowerCase().includes(q) || (a.alias || '').toLowerCase().includes(q)
    }).slice(0, 8)

    const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const newValue = e.target.value
        onChange(newValue)

        const cursorPos = e.target.selectionStart || 0
        const textBeforeCursor = newValue.slice(0, cursorPos)
        const atMatch = textBeforeCursor.match(/@([A-Za-z0-9_\-一-鿿]*)$/)

        if (atMatch) {
            mentionStartRef.current = cursorPos - atMatch[1].length - 1
            setQuery(atMatch[1])
            setSelectedIndex(0)
            setShowDropdown(true)
        } else {
            setShowDropdown(false)
        }
    }

    const insertMention = (asset: ReferenceAsset) => {
        const start = mentionStartRef.current
        if (start < 0) return
        const ta = textareaRef.current
        const cursorPos = ta?.selectionStart || value.length
        const before = value.slice(0, start)
        const after = value.slice(cursorPos)
        const newValue = `${before}@${asset.name} ${after}`
        onChange(newValue)
        setShowDropdown(false)

        requestAnimationFrame(() => {
            if (ta) {
                const newPos = start + asset.name.length + 2
                ta.selectionStart = newPos
                ta.selectionEnd = newPos
                ta.focus()
            }
        })
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (!showDropdown || filtered.length === 0) return

        if (e.key === 'ArrowDown') {
            e.preventDefault()
            setSelectedIndex(i => (i + 1) % filtered.length)
        } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setSelectedIndex(i => (i - 1 + filtered.length) % filtered.length)
        } else if (e.key === 'Enter') {
            e.preventDefault()
            insertMention(filtered[selectedIndex])
        } else if (e.key === 'Escape') {
            setShowDropdown(false)
        }
    }

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setShowDropdown(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    return (
        <div className="relative flex-1">
            <textarea
                ref={textareaRef}
                value={value}
                onChange={handleInput}
                onKeyDown={handleKeyDown}
                placeholder={placeholder}
                className={cn(
                    "w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground",
                    className
                )}
                rows={2}
            />
            {showDropdown && filtered.length > 0 && (
                <div
                    ref={dropdownRef}
                    className="absolute z-50 w-64 rounded-md border bg-popover shadow-lg"
                    style={{ bottom: '100%', left: 0, marginBottom: 4 }}
                >
                    {filtered.map((asset, i) => (
                        <div
                            key={asset.id}
                            className={cn(
                                "flex items-center gap-2 px-3 py-2 text-sm cursor-pointer",
                                i === selectedIndex && "bg-accent"
                            )}
                            onMouseDown={(e) => { e.preventDefault(); insertMention(asset) }}
                            onMouseEnter={() => setSelectedIndex(i)}
                        >
                            {asset.thumbnail_path ? (
                                <img src={asset.thumbnail_path} className="h-6 w-6 rounded object-cover" alt="" />
                            ) : (
                                <div className="h-6 w-6 rounded bg-muted flex items-center justify-center text-xs">
                                    {asset.name[0]}
                                </div>
                            )}
                            <div className="flex-1 min-w-0">
                                <div className="truncate font-medium">{asset.name}</div>
                                {asset.alias && <div className="truncate text-xs text-muted-foreground">{asset.alias}</div>}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
