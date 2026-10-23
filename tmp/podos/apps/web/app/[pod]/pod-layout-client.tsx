"use client";

import { useParams } from "next/navigation";
import { WorkspaceShell } from "@/components/shell/workspace-shell";

export default function PodLayoutClient({ children }: { children: React.ReactNode }) {
  const { pod } = useParams<{ pod: string }>();
  return <WorkspaceShell pod={pod}>{children}</WorkspaceShell>;
}
