import type { Metadata } from 'next'

import { AppShell } from '@/components/app-shell'
import { I18nProvider } from '@/components/i18n-provider'
import { LoginGate } from '@/components/login-gate'
import { ToastProvider } from '@/components/toast'
import './fonts.css'
import './globals.css'

export const metadata: Metadata = {
  title: '赵露思 上号系统',
  description: '本地指纹浏览器、代理与 Claude Team 上号管理',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <I18nProvider>
          <LoginGate>
            <ToastProvider>
              <AppShell>{children}</AppShell>
            </ToastProvider>
          </LoginGate>
        </I18nProvider>
      </body>
    </html>
  )
}
