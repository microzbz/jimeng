import {
    LayoutDashboard,
    Zap,
    Users,
    ShieldCheck,
    Globe,
    Mail,
    History,
    Settings,
    Sparkles,
    Video
} from 'lucide-react';
import Dashboard from '../pages/Dashboard';
import Tasks from '../pages/Tasks';
import Accounts from '../pages/Accounts';
import Proxies from '../pages/Proxies';
import Domains from '../pages/Domains';
import OutlookMailboxes from '../pages/OutlookMailboxes';
import Logs from '../pages/Logs';
import SettingsPage from '../pages/Settings';
import ContentGeneration from '../pages/ContentGeneration';
import FastReference from '../pages/FastReference';

export interface RouteConfig {
    path: string;
    title: string;
    i18nKey: string;
    element: React.ReactNode;
    icon?: any;
    showInSidebar?: boolean;
    section?: string;
}

export const routes: RouteConfig[] = [
    {
        path: '/',
        title: 'Dashboard',
        i18nKey: 'nav.dashboard',
        element: <Dashboard />,
        icon: LayoutDashboard,
        showInSidebar: true,
        section: 'Core'
    },
    {
        path: '/tasks',
        title: 'Registration Tasks',
        i18nKey: 'nav.tasks',
        element: <Tasks />,
        icon: Zap,
        showInSidebar: true,
        section: 'Core'
    },
    {
        path: '/accounts',
        title: 'Account Management',
        i18nKey: 'nav.accounts',
        element: <Accounts />,
        icon: Users,
        showInSidebar: true,
        section: 'Management'
    },
    {
        path: '/proxies',
        title: 'Proxy Pool',
        i18nKey: 'nav.proxies',
        element: <Proxies />,
        icon: ShieldCheck,
        showInSidebar: true,
        section: 'Management'
    },
    {
        path: '/domains',
        title: 'Domain Center',
        i18nKey: 'nav.domains',
        element: <Domains />,
        icon: Globe,
        showInSidebar: true,
        section: 'Management'
    },
    {
        path: '/outlook-mailboxes',
        title: 'Outlook Mailboxes',
        i18nKey: 'nav.outlook_mailboxes',
        element: <OutlookMailboxes />,
        icon: Mail,
        showInSidebar: true,
        section: 'Management'
    },
    {
        path: '/content',
        title: 'Content Generation',
        i18nKey: 'nav.generate',
        element: <ContentGeneration />,
        icon: Sparkles,
        showInSidebar: true,
        section: 'Core'
    },
    {
        path: '/fast-reference',
        title: 'Fast Reference Video',
        i18nKey: 'nav.fast_reference',
        element: <FastReference />,
        icon: Video,
        showInSidebar: true,
        section: 'Core'
    },
    {
        path: '/logs',
        title: 'Running Logs',
        i18nKey: 'nav.logs',
        element: <Logs />,
        icon: History,
        showInSidebar: true,
        section: 'Monitoring'
    },
    {
        path: '/settings',
        title: 'Settings',
        i18nKey: 'nav.settings',
        element: <SettingsPage />,
        icon: Settings,
        showInSidebar: true,
        section: 'Settings'
    }
];
