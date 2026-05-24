import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Inter } from "next/font/google";

import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/Topbar";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";

import "./globals.css";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "TracePilot",
    template: "%s · TracePilot",
  },
  description:
    "Self-hosted AI engineering copilot — grounded code Q&A, debugging, and change review over your repositories.",
  applicationName: "TracePilot",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#090b11",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} dark`}>
      <body className="bg-bg font-sans text-fg">
        <WorkspaceProvider>
          <div className="flex h-screen w-full overflow-hidden">
            <Sidebar />
            <div className="flex min-w-0 flex-1 flex-col">
              <Topbar />
              <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
            </div>
          </div>
        </WorkspaceProvider>
      </body>
    </html>
  );
}
