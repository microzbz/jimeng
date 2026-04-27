import { useEffect, useMemo, useState, useRef, useCallback } from 'react'
import { VirtuosoGrid } from 'react-virtuoso'
import { Zap, Send, Trash2, RotateCcw, Upload, Image, X, Clock, MonitorPlay, Scaling, FolderOpen } from 'lucide-react'
import { fastReferenceApi, ContentGenerationJob, ReferenceAsset, FastReferenceJobRequest } from '@/services/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { useLanguage } from '@/contexts/LanguageContext'
import MentionInput from '@/components/MentionInput'
import { toast } from 'sonner'

let cachedJobs: ContentGenerationJob[] = []
let cachedAssets: ReferenceAsset[] = []

export default function FastReference() {
    const { t } = useLanguage()
    const [jobs, setJobs] = useState<ContentGenerationJob[]>(cachedJobs)
    const [assets, setAssets] = useState<ReferenceAsset[]>(cachedAssets)
    const [prompt, setPrompt] = useState('')
    const [model] = useState('Dreamina Seedance 2.0 Fast')
    const [duration, setDuration] = useState(5)
    const [resolution, setResolution] = useState('720p')
    const [ratio, setRatio] = useState('16:9')
    const [filterStatus, setFilterStatus] = useState<string>('all')
    const [previewJob, setPreviewJob] = useState<ContentGenerationJob | null>(null)
    const [isHovered, setIsHovered] = useState(false)
    const [assetSheetOpen, setAssetSheetOpen] = useState(false)
    const pollRef = useRef<ReturnType<typeof setInterval>>()

    const fetchJobs = useCallback(async () => {
        try {
            const data = await fastReferenceApi.listJobs({ page_size: 100 })
            setJobs(data)
            cachedJobs = data
        } catch {}
    }, [])

    const fetchAssets = useCallback(async () => {
        try {
            const data = await fastReferenceApi.listAssets()
            setAssets(data)
            cachedAssets = data
        } catch {}
    }, [])

    useEffect(() => {
        fetchJobs()
        fetchAssets()
        pollRef.current = setInterval(fetchJobs, 4000)
        const onVis = () => {
            if (document.hidden) {
                clearInterval(pollRef.current)
            } else {
                fetchJobs()
                pollRef.current = setInterval(fetchJobs, 4000)
            }
        }
        document.addEventListener('visibilitychange', onVis)
        return () => {
            clearInterval(pollRef.current)
            document.removeEventListener('visibilitychange', onVis)
        }
    }, [fetchJobs, fetchAssets])

    const filteredJobs = useMemo(() => {
        if (filterStatus === 'all') return jobs
        return jobs.filter(j => j.status === filterStatus)
    }, [jobs, filterStatus])

    const handleGenerate = async () => {
        if (!prompt.trim()) return
        try {
            const payload: FastReferenceJobRequest = { prompt, model, duration, resolution, ratio }
            await fastReferenceApi.createJob(payload)
            setPrompt('')
            fetchJobs()
            toast.success(t('fast_ref.job_created'))
        } catch (e: any) {
            toast.error(e.message)
        }
    }

    const handleRetry = async (job: ContentGenerationJob) => {
        try {
            await fastReferenceApi.retryJob(job.id)
            fetchJobs()
        } catch (e: any) {
            toast.error(e.message)
        }
    }

    const handleDelete = async (job: ContentGenerationJob) => {
        try {
            await fastReferenceApi.deleteJob(job.id)
            fetchJobs()
        } catch (e: any) {
            toast.error(e.message)
        }
    }

    const handleUploadAsset = async (files: FileList) => {
        for (const file of Array.from(files)) {
            const fd = new FormData()
            fd.append('file', file)
            fd.append('name', file.name.replace(/\.[^.]+$/, ''))
            fd.append('asset_type', file.type.startsWith('video') ? 'video' : 'image')
            try {
                await fastReferenceApi.uploadAsset(fd)
                toast.success(`${file.name} uploaded`)
            } catch (e: any) {
                toast.error(e.message)
            }
        }
        fetchAssets()
    }

    const handleDeleteAsset = async (id: number) => {
        try {
            await fastReferenceApi.deleteAsset(id)
            fetchAssets()
        } catch (e: any) {
            toast.error(e.message)
        }
    }

    const panelExpanded = isHovered || prompt.length > 0

    const statusColor = (s: string) => {
        switch (s) {
            case 'success': return 'bg-emerald-500/20 text-emerald-400'
            case 'failed': return 'bg-red-500/20 text-red-400'
            case 'processing': case 'submitted': return 'bg-blue-500/20 text-blue-400'
            case 'queued': case 'submitting': return 'bg-yellow-500/20 text-yellow-400'
            case 'cancelled': return 'bg-zinc-500/20 text-zinc-400'
            default: return 'bg-zinc-500/20 text-zinc-400'
        }
    }

    return (
        <div className="h-full flex flex-col relative">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/5">
                <div className="flex items-center gap-3">
                    <Zap className="w-5 h-5 text-amber-400" />
                    <h1 className="text-lg font-semibold text-white">{t('fast_ref.title')}</h1>
                    <Badge variant="outline" className="text-xs">{filteredJobs.length} {t('fast_ref.jobs')}</Badge>
                </div>
                <div className="flex items-center gap-2">
                    <Select value={filterStatus} onValueChange={setFilterStatus}>
                        <SelectTrigger className="w-32 h-8 text-xs bg-white/5 border-white/10">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">{t('common.all')}</SelectItem>
                            <SelectItem value="queued">Queued</SelectItem>
                            <SelectItem value="processing">Processing</SelectItem>
                            <SelectItem value="success">Success</SelectItem>
                            <SelectItem value="failed">Failed</SelectItem>
                        </SelectContent>
                    </Select>
                    <Dialog open={assetSheetOpen} onOpenChange={setAssetSheetOpen}>
                        <DialogTrigger asChild>
                            <Button variant="outline" size="sm" className="gap-1.5">
                                <FolderOpen className="w-4 h-4" />
                                {t('fast_ref.asset_library')}
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-md bg-zinc-950 border-white/10">
                            <DialogTitle className="text-white">{t('fast_ref.asset_library')}</DialogTitle>
                            <div className="mt-4 space-y-3">
                                <label className="flex items-center justify-center w-full h-24 border-2 border-dashed border-white/10 rounded-lg cursor-pointer hover:border-white/20 transition-colors">
                                    <input type="file" className="hidden" multiple accept="image/*,video/*"
                                        onChange={e => e.target.files && handleUploadAsset(e.target.files)} />
                                    <div className="text-center text-zinc-400 text-sm">
                                        <Upload className="w-5 h-5 mx-auto mb-1" />
                                        {t('fast_ref.drop_upload')}
                                    </div>
                                </label>
                                <div className="space-y-2 max-h-[50vh] overflow-y-auto">
                                    {assets.map(asset => (
                                        <div key={asset.id} className="flex items-center gap-3 p-2 rounded-lg bg-white/5 group">
                                            {asset.thumbnail_path ? (
                                                <img src={`/${asset.thumbnail_path}`} className="w-10 h-10 rounded object-cover" alt="" />
                                            ) : (
                                                <div className="w-10 h-10 rounded bg-zinc-800 flex items-center justify-center">
                                                    <Image className="w-4 h-4 text-zinc-500" />
                                                </div>
                                            )}
                                            <div className="flex-1 min-w-0">
                                                <div className="text-sm text-white truncate">{asset.name}</div>
                                                <div className="text-xs text-zinc-500">{asset.asset_type} · {(asset.file_size / 1024).toFixed(0)}KB</div>
                                            </div>
                                            <button onClick={() => handleDeleteAsset(asset.id)}
                                                className="opacity-0 group-hover:opacity-100 p-1 text-zinc-500 hover:text-red-400 transition-all">
                                                <X className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    ))}
                                    {assets.length === 0 && (
                                        <div className="text-center text-zinc-500 text-sm py-8">{t('fast_ref.no_assets')}</div>
                                    )}
                                </div>
                            </div>
                        </DialogContent>
                    </Dialog>
                </div>
            </div>

            {/* Job Grid */}
            <div className="flex-1 overflow-hidden px-6 pt-4 pb-40">
                {filteredJobs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-zinc-500">
                        <Zap className="w-12 h-12 mb-3 opacity-30" />
                        <p className="text-sm">{t('fast_ref.empty')}</p>
                    </div>
                ) : (
                    <VirtuosoGrid
                        totalCount={filteredJobs.length}
                        overscan={200}
                        listClassName="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3"
                        itemContent={index => {
                            const job = filteredJobs[index]
                            return (
                                <Card key={job.id} className="bg-white/[0.03] border-white/5 overflow-hidden group cursor-pointer hover:border-white/10 transition-all"
                                    onClick={() => job.status === 'success' && setPreviewJob(job)}>
                                    <div className="aspect-video relative bg-zinc-900">
                                        {job.status === 'success' && job.output_urls?.[0] ? (
                                            <video src={job.output_urls[0]} className="w-full h-full object-cover"
                                                muted loop onMouseEnter={e => (e.target as HTMLVideoElement).play()}
                                                onMouseLeave={e => { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0 }} />
                                        ) : job.status === 'failed' ? (
                                            <div className="w-full h-full flex items-center justify-center">
                                                <span className="text-red-400 text-xs text-center px-2">{job.error_message || 'Failed'}</span>
                                            </div>
                                        ) : (
                                            <div className="w-full h-full flex items-center justify-center">
                                                <div className="w-6 h-6 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
                                            </div>
                                        )}
                                        <Badge className={`absolute top-1.5 left-1.5 text-[10px] ${statusColor(job.status)}`}>
                                            {job.status}
                                        </Badge>
                                        <div className="absolute bottom-0 inset-x-0 p-1.5 bg-gradient-to-t from-black/80 to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex gap-1 justify-end">
                                            {job.status === 'failed' && (
                                                <button onClick={e => { e.stopPropagation(); handleRetry(job) }}
                                                    className="p-1 rounded bg-white/10 hover:bg-white/20 text-white">
                                                    <RotateCcw className="w-3.5 h-3.5" />
                                                </button>
                                            )}
                                            <button onClick={e => { e.stopPropagation(); handleDelete(job) }}
                                                className="p-1 rounded bg-white/10 hover:bg-red-500/50 text-white">
                                                <Trash2 className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    </div>
                                    <CardContent className="p-2">
                                        <p className="text-xs text-zinc-400 truncate">{job.prompt}</p>
                                    </CardContent>
                                </Card>
                            )
                        }}
                    />
                )}
            </div>

            {/* Bottom Input Panel */}
            <div className={`fixed bottom-0 left-64 right-0 z-40 transition-all duration-300 ${panelExpanded ? 'pb-4' : 'pb-2'}`}
                onMouseEnter={() => setIsHovered(true)} onMouseLeave={() => setIsHovered(false)}>
                <div className={`mx-6 rounded-2xl border border-white/10 bg-zinc-950/80 backdrop-blur-xl shadow-2xl transition-all duration-300 ${panelExpanded ? 'p-4' : 'p-3'}`}>
                    <div className="flex items-end gap-3">
                        <div className="flex-1">
                            <MentionInput
                                value={prompt}
                                onChange={setPrompt}
                                assets={assets}
                                placeholder={t('fast_ref.prompt_placeholder')}
                                className="w-full bg-transparent border-0 text-white placeholder-zinc-500 text-sm resize-none focus:outline-none focus:ring-0"
                            />
                        </div>
                        <Button size="sm" onClick={handleGenerate} disabled={!prompt.trim()}
                            className="bg-amber-500 hover:bg-amber-600 text-black font-medium gap-1.5 shrink-0">
                            <Send className="w-4 h-4" />
                            {t('fast_ref.generate')}
                        </Button>
                    </div>
                    {panelExpanded && (
                        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-white/5">
                            <div className="flex items-center gap-1.5 text-xs text-zinc-400">
                                <Clock className="w-3.5 h-3.5" />
                                <Select value={String(duration)} onValueChange={v => setDuration(Number(v))}>
                                    <SelectTrigger className="h-7 w-16 text-xs bg-white/5 border-white/10"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="5">5s</SelectItem>
                                        <SelectItem value="10">10s</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex items-center gap-1.5 text-xs text-zinc-400">
                                <MonitorPlay className="w-3.5 h-3.5" />
                                <Select value={resolution} onValueChange={setResolution}>
                                    <SelectTrigger className="h-7 w-20 text-xs bg-white/5 border-white/10"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="720p">720p</SelectItem>
                                        <SelectItem value="1080p">1080p</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex items-center gap-1.5 text-xs text-zinc-400">
                                <Scaling className="w-3.5 h-3.5" />
                                <Select value={ratio} onValueChange={setRatio}>
                                    <SelectTrigger className="h-7 w-20 text-xs bg-white/5 border-white/10"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="16:9">16:9</SelectItem>
                                        <SelectItem value="9:16">9:16</SelectItem>
                                        <SelectItem value="1:1">1:1</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Preview Dialog */}
            {previewJob && (
                <Dialog open={!!previewJob} onOpenChange={() => setPreviewJob(null)}>
                    <DialogContent className="max-w-4xl bg-zinc-950 border-white/10">
                        <DialogTitle className="text-white">{t('fast_ref.preview')}</DialogTitle>
                        <div className="flex gap-4">
                            <div className="flex-1">
                                {previewJob.output_urls?.[0] && (
                                    <video src={previewJob.output_urls[0]} controls autoPlay className="w-full rounded-lg" />
                                )}
                            </div>
                            <div className="w-64 space-y-3 text-sm">
                                <div><span className="text-zinc-500">Prompt:</span><p className="text-white mt-1">{previewJob.prompt}</p></div>
                                <div><span className="text-zinc-500">Model:</span><p className="text-white">{previewJob.model}</p></div>
                                <div><span className="text-zinc-500">Duration:</span><p className="text-white">{previewJob.duration}s</p></div>
                                <div><span className="text-zinc-500">Resolution:</span><p className="text-white">{previewJob.resolution}</p></div>
                                <div><span className="text-zinc-500">Ratio:</span><p className="text-white">{previewJob.ratio}</p></div>
                                <div><span className="text-zinc-500">Region:</span><p className="text-white">{previewJob.region || '-'}</p></div>
                            </div>
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    )
}
