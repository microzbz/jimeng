import { useEffect, useState, useRef } from 'react'
import {
    Video, Send, Trash2, RotateCcw, Upload, X, Plus,
    Loader2, AlertCircle, Package
} from 'lucide-react'
import { toast } from 'sonner'
import {
    fastReferenceApi, FastReferenceJob, ReferenceAsset, FastReferenceJobRequest
} from '@/services/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useLanguage } from '@/contexts/LanguageContext'
import MentionInput from '@/components/MentionInput'

const POLL_INTERVAL_MS = 4000
let cachedJobs: FastReferenceJob[] = []
let cachedAssets: ReferenceAsset[] = []

export default function FastReference() {
    const { t } = useLanguage()
    const [jobs, setJobs] = useState<FastReferenceJob[]>(() => cachedJobs)
    const [assets, setAssets] = useState<ReferenceAsset[]>(() => cachedAssets)
    const [prompt, setPrompt] = useState('')
    const [model, setModel] = useState('Seedance 2.0 Fast')
    const [duration, setDuration] = useState(5)
    const [resolution, setResolution] = useState('720p')
    const [ratio, setRatio] = useState('16:9')
    const [submitting, setSubmitting] = useState(false)
    const [isHovered, setIsHovered] = useState(false)

    const [assetDialogOpen, setAssetDialogOpen] = useState(false)
    const [uploadName, setUploadName] = useState('')
    const [uploadAlias, setUploadAlias] = useState('')
    const [uploadFile, setUploadFile] = useState<File | null>(null)

    const [previewJob, setPreviewJob] = useState<FastReferenceJob | null>(null)

    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
    const fetchingJobsRef = useRef(false)

    const fetchJobs = async () => {
        if (fetchingJobsRef.current) return
        fetchingJobsRef.current = true
        try {
            const data = await fastReferenceApi.listJobs({ page_size: 100 })
            cachedJobs = data.items
            setJobs(data.items)
        } catch (e) {
            console.error('Failed to fetch jobs', e)
        } finally {
            fetchingJobsRef.current = false
        }
    }

    const fetchAssets = async () => {
        try {
            const data = await fastReferenceApi.listAssets({ page_size: 200 })
            cachedAssets = data.items
            setAssets(data.items)
        } catch (e) {
            console.error('Failed to fetch assets', e)
        }
    }

    useEffect(() => {
        fetchJobs()
        fetchAssets()
        pollRef.current = setInterval(fetchJobs, POLL_INTERVAL_MS)

        const handleVisibility = () => {
            if (document.hidden) {
                if (pollRef.current) clearInterval(pollRef.current)
            } else {
                fetchJobs()
                pollRef.current = setInterval(fetchJobs, POLL_INTERVAL_MS)
            }
        }
        document.addEventListener('visibilitychange', handleVisibility)
        return () => {
            if (pollRef.current) clearInterval(pollRef.current)
            document.removeEventListener('visibilitychange', handleVisibility)
        }
    }, [])

    const handleGenerate = async () => {
        if (!prompt.trim()) return
        setSubmitting(true)
        try {
            const req: FastReferenceJobRequest = {
                prompt: prompt.trim(),
                model,
                duration,
                resolution,
                ratio,
            }
            await fastReferenceApi.createJob(req)
            toast.success('Job created')
            setPrompt('')
            fetchJobs()
        } catch (error: any) {
            toast.error(error.message || 'Failed to create job')
        } finally {
            setSubmitting(false)
        }
    }

    const handleRetry = async (id: number) => {
        try {
            await fastReferenceApi.retryJob(id)
            toast.success('Job re-queued')
            fetchJobs()
        } catch (error: any) {
            toast.error(error.message || 'Retry failed')
        }
    }

    const handleDelete = async (id: number) => {
        try {
            await fastReferenceApi.deleteJob(id)
            toast.success('Job deleted')
            fetchJobs()
        } catch (error: any) {
            toast.error(error.message || 'Delete failed')
        }
    }

    const handleUploadAsset = async () => {
        if (!uploadFile) return
        const formData = new FormData()
        formData.append('file', uploadFile)
        if (uploadName) formData.append('name', uploadName)
        if (uploadAlias) formData.append('alias', uploadAlias)
        try {
            await fastReferenceApi.uploadAsset(formData)
            toast.success('Asset uploaded')
            setUploadFile(null)
            setUploadName('')
            setUploadAlias('')
            fetchAssets()
        } catch (error: any) {
            toast.error(error.message || 'Upload failed')
        }
    }

    const handleDeleteAsset = async (id: number) => {
        try {
            await fastReferenceApi.deleteAsset(id)
            toast.success('Asset deleted')
            fetchAssets()
        } catch (error: any) {
            toast.error(error.message || 'Delete failed')
        }
    }

    const getStatusBadge = (status: string) => {
        const map: Record<string, { variant: 'default' | 'secondary' | 'destructive' | 'outline'; label: string }> = {
            queued: { variant: 'secondary', label: t('fast_ref.status.queued') },
            submitting: { variant: 'outline', label: t('fast_ref.status.submitting') },
            submitted: { variant: 'outline', label: t('fast_ref.status.submitted') },
            processing: { variant: 'default', label: t('fast_ref.status.processing') },
            success: { variant: 'default', label: t('fast_ref.status.success') },
            failed: { variant: 'destructive', label: t('fast_ref.status.failed') },
        }
        const info = map[status] || { variant: 'secondary' as const, label: status }
        return <Badge variant={info.variant}>{info.label}</Badge>
    }

    const panelExpanded = isHovered || prompt.length > 0

    return (
        <div className="flex flex-col h-full relative">
            <div className="p-6 pb-2">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-bold">{t('fast_ref.title')}</h1>
                        <p className="text-sm text-muted-foreground">{t('fast_ref.subtitle')}</p>
                    </div>
                    <Button variant="outline" onClick={() => setAssetDialogOpen(true)}>
                        <Package className="mr-2 h-4 w-4" />
                        {t('fast_ref.assets')} ({assets.length})
                    </Button>
                </div>
            </div>

            {/* Job grid */}
            <div className="flex-1 overflow-auto px-6 pb-48">
                {jobs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
                        <Video className="h-12 w-12 mb-4 opacity-30" />
                        <p>{t('fast_ref.no_jobs')}</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                        {jobs.map(job => (
                            <JobCard
                                key={job.id}
                                job={job}
                                onRetry={handleRetry}
                                onDelete={handleDelete}
                                onPreview={setPreviewJob}
                                getStatusBadge={getStatusBadge}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Bottom input panel */}
            <div
                className={cn(
                    "fixed bottom-0 left-0 right-0 z-40 transition-all duration-300 ml-64",
                    "bg-background/60 backdrop-blur-2xl border-t border-white/10 shadow-[0_-4px_20px_rgba(0,0,0,0.3)]",
                    panelExpanded ? "pb-6 pt-4" : "pb-4 pt-3"
                )}
                onMouseEnter={() => setIsHovered(true)}
                onMouseLeave={() => setIsHovered(false)}
            >
                <div className="max-w-4xl mx-auto px-6">
                    <div className="flex items-end gap-3">
                        <MentionInput
                            value={prompt}
                            onChange={setPrompt}
                            assets={assets}
                            placeholder={t('fast_ref.prompt_placeholder')}
                            className="min-h-[40px]"
                        />
                        <Button
                            size="sm"
                            onClick={handleGenerate}
                            disabled={submitting || !prompt.trim()}
                        >
                            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        </Button>
                    </div>
                    {panelExpanded && (
                        <div className="flex items-center gap-3 mt-3 flex-wrap">
                            <Select value={model} onValueChange={setModel}>
                                <SelectTrigger className="w-48 h-8 text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="Seedance 2.0 Fast">Seedance 2.0 Fast</SelectItem>
                                    <SelectItem value="Seedance 2.0">Seedance 2.0</SelectItem>
                                </SelectContent>
                            </Select>
                            <Select value={String(duration)} onValueChange={v => setDuration(Number(v))}>
                                <SelectTrigger className="w-20 h-8 text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {[5, 10].map(d => (
                                        <SelectItem key={d} value={String(d)}>{d}s</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <Select value={resolution} onValueChange={setResolution}>
                                <SelectTrigger className="w-24 h-8 text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="720p">720p</SelectItem>
                                    <SelectItem value="1080p">1080p</SelectItem>
                                </SelectContent>
                            </Select>
                            <Select value={ratio} onValueChange={setRatio}>
                                <SelectTrigger className="w-24 h-8 text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="1:1">1:1</SelectItem>
                                    <SelectItem value="16:9">16:9</SelectItem>
                                    <SelectItem value="9:16">9:16</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    )}
                </div>
            </div>

            {/* Asset Library Dialog */}
            <Dialog open={assetDialogOpen} onOpenChange={setAssetDialogOpen}>
                <DialogContent className="sm:max-w-[700px] max-h-[80vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>{t('fast_ref.assets')}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="flex items-end gap-2">
                            <div className="flex-1 space-y-1">
                                <label className="text-xs text-muted-foreground">{t('fast_ref.asset.name')}</label>
                                <Input
                                    value={uploadName}
                                    onChange={e => setUploadName(e.target.value)}
                                    placeholder="asset_name"
                                    className="h-8"
                                />
                            </div>
                            <div className="flex-1 space-y-1">
                                <label className="text-xs text-muted-foreground">{t('fast_ref.asset.alias')}</label>
                                <Input
                                    value={uploadAlias}
                                    onChange={e => setUploadAlias(e.target.value)}
                                    placeholder="alias1, alias2"
                                    className="h-8"
                                />
                            </div>
                            <label className="cursor-pointer">
                                <input
                                    type="file"
                                    accept="image/*"
                                    className="hidden"
                                    onChange={e => setUploadFile(e.target.files?.[0] || null)}
                                />
                                <div className="flex items-center gap-1 px-3 py-1.5 rounded-md border text-xs hover:bg-accent">
                                    <Upload className="h-3 w-3" />
                                    {uploadFile ? uploadFile.name.slice(0, 15) : 'Choose'}
                                </div>
                            </label>
                            <Button size="sm" onClick={handleUploadAsset} disabled={!uploadFile}>
                                <Plus className="h-3 w-3" />
                            </Button>
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                            {assets.map(asset => (
                                <div key={asset.id} className="group relative rounded-lg border p-2">
                                    {asset.thumbnail_path ? (
                                        <img src={asset.thumbnail_path} className="w-full h-24 object-cover rounded" alt={asset.name} />
                                    ) : (
                                        <div className="w-full h-24 bg-muted rounded flex items-center justify-center text-2xl">
                                            {asset.name[0]}
                                        </div>
                                    )}
                                    <div className="mt-1">
                                        <div className="text-xs font-medium truncate">@{asset.name}</div>
                                        {asset.alias && <div className="text-xs text-muted-foreground truncate">{asset.alias}</div>}
                                    </div>
                                    <button
                                        className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 p-1 rounded bg-destructive/80 text-white"
                                        onClick={() => handleDeleteAsset(asset.id)}
                                    >
                                        <X className="h-3 w-3" />
                                    </button>
                                </div>
                            ))}
                        </div>
                        {assets.length === 0 && (
                            <p className="text-center text-sm text-muted-foreground py-8">No assets yet. Upload your first reference image.</p>
                        )}
                    </div>
                </DialogContent>
            </Dialog>

            {/* Preview Dialog */}
            <Dialog open={!!previewJob} onOpenChange={() => setPreviewJob(null)}>
                <DialogContent className="sm:max-w-[800px]">
                    <DialogHeader>
                        <DialogTitle>Job #{previewJob?.id}</DialogTitle>
                    </DialogHeader>
                    {previewJob && (
                        <div className="space-y-4">
                            {previewJob.video_url || (previewJob.local_urls && previewJob.local_urls.length > 0) ? (
                                <video
                                    src={previewJob.local_urls?.[0] ? `/outputs/${previewJob.local_urls[0].split('/').pop()}` : previewJob.video_url}
                                    controls
                                    autoPlay
                                    className="w-full rounded-lg"
                                />
                            ) : (
                                <div className="h-64 bg-muted rounded-lg flex items-center justify-center">
                                    <p className="text-muted-foreground">{previewJob.status === 'failed' ? previewJob.error_message : 'Processing...'}</p>
                                </div>
                            )}
                            <div className="grid grid-cols-2 gap-2 text-sm">
                                <div><span className="text-muted-foreground">Prompt:</span> {previewJob.prompt}</div>
                                <div><span className="text-muted-foreground">Model:</span> {previewJob.model}</div>
                                <div><span className="text-muted-foreground">Status:</span> {previewJob.status}</div>
                                <div><span className="text-muted-foreground">Retries:</span> {previewJob.retry_count}</div>
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    )
}

function JobCard({
    job,
    onRetry,
    onDelete,
    onPreview,
    getStatusBadge,
}: {
    job: FastReferenceJob
    onRetry: (id: number) => void
    onDelete: (id: number) => void
    onPreview: (job: FastReferenceJob) => void
    getStatusBadge: (status: string) => React.ReactNode
}) {
    const isActive = ['queued', 'submitting', 'submitted', 'processing'].includes(job.status)
    const hasVideo = (job.local_urls && job.local_urls.length > 0) || job.video_url

    return (
        <Card
            className={cn(
                "group relative overflow-hidden cursor-pointer transition-shadow hover:shadow-lg",
                isActive && "ring-1 ring-primary/20"
            )}
            onClick={() => onPreview(job)}
        >
            <CardContent className="p-0">
                <div className="relative aspect-video bg-muted">
                    {hasVideo ? (
                        <video
                            src={job.local_urls?.[0] ? `/outputs/${job.local_urls[0].split('/').pop()}` : job.video_url}
                            className="w-full h-full object-cover"
                            muted
                            onMouseEnter={e => (e.target as HTMLVideoElement).play().catch(() => {})}
                            onMouseLeave={e => { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0 }}
                        />
                    ) : isActive ? (
                        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
                            <Loader2 className="h-8 w-8 animate-spin text-primary/50" />
                            <span className="text-xs text-muted-foreground">{job.status}</span>
                        </div>
                    ) : job.status === 'failed' ? (
                        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
                            <AlertCircle className="h-8 w-8 text-destructive/50" />
                            <span className="text-xs text-destructive truncate max-w-[80%]">{job.error_message || 'Failed'}</span>
                        </div>
                    ) : null}
                </div>
                <div className="p-3">
                    <p className="text-xs truncate mb-1">{job.prompt}</p>
                    <div className="flex items-center justify-between">
                        {getStatusBadge(job.status)}
                        <span className="text-xs text-muted-foreground">
                            {job.created_at ? new Date(job.created_at).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}
                        </span>
                    </div>
                </div>
                <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={e => e.stopPropagation()}>
                    {job.status === 'failed' && (
                        <button className="p-1.5 rounded-full bg-background/80 hover:bg-background" onClick={() => onRetry(job.id)}>
                            <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                    )}
                    {!isActive && (
                        <button className="p-1.5 rounded-full bg-destructive/80 hover:bg-destructive text-white" onClick={() => onDelete(job.id)}>
                            <Trash2 className="h-3.5 w-3.5" />
                        </button>
                    )}
                </div>
            </CardContent>
        </Card>
    )
}
