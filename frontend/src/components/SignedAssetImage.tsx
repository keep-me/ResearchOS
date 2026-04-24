import type { ImgHTMLAttributes } from "react";
import { useSignedApiAssetUrl } from "@/hooks/useSignedApiAssetUrl";

type SignedAssetImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  src: string | null | undefined;
};

export default function SignedAssetImage({ src, ...props }: SignedAssetImageProps) {
  const resolvedSrc = useSignedApiAssetUrl(src);
  return <img {...props} src={resolvedSrc || undefined} />;
}
