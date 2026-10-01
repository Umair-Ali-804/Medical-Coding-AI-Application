import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Medical Coding AI",
  description: "Evidence-backed ICD-10-CM coding with deterministic validation and human review",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
