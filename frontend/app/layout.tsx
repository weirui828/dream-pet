import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Nav from "@/components/Nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Dream Pet",
  description: "A self-hosted AI companion that gets bored, explores, chats and dreams.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <div className="mx-auto flex min-h-screen max-w-[1400px] flex-col md:flex-row">
          <Nav />
          <main className="min-w-0 flex-1 px-4 py-5 md:px-8 md:py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
