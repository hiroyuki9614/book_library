import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface MiniCardProps {
  title: string;
  description?: string;
  children: React.ReactNode;
}

export function MiniCard({ title, description, children }: MiniCardProps) {
  return (
    <Card className="flex-1 bg-card">
      <CardContent className="flex items-center gap-4 p-6">
        <div className="rounded-lg bg-primary/10 p-3"></div>

        <div className="flex flex-col">
          <CardTitle>{title}</CardTitle>

          {description && <CardDescription>{description}</CardDescription>}

          <div className="mt-2">{children}</div>
        </div>
      </CardContent>
    </Card>
  );
}
