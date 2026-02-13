import React from "react";
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame
} from "remotion";

export type TransitionType = "cut" | "fade";

export type ShotTransition = {
  type: TransitionType;
  duration_frames: number;
};

export type Shot = {
  shot_id: string;
  start_frame: number;
  duration_frames: number;
  video_src: string;
  subtitle_lines: string[];
  transition_to_next: ShotTransition;
  extend_mode: "none" | "freeze_last_subtle_zoom";
  trim_policy: "none" | "from_start";
};

export type VideoCompositionProps = {
  fps: number;
  width: number;
  height: number;
  total_frames: number;
  shots: Shot[];
};

const subtitleBaseStyle: React.CSSProperties = {
  position: "absolute",
  left: "8%",
  right: "8%",
  bottom: "8%",
  color: "#ffffff",
  textAlign: "center",
  fontFamily: "'Noto Sans JP', 'Yu Gothic UI', sans-serif",
  fontWeight: 700,
  lineHeight: 1.35,
  letterSpacing: "0.03em",
  textShadow: "0 0 6px rgba(0,0,0,0.75), 0 0 18px rgba(0,0,0,0.5)"
};

const lineStyle: React.CSSProperties = {
  fontSize: 58,
  marginTop: 2,
  marginBottom: 2
};

const ShotLayer: React.FC<{
  shot: Shot;
  fadeInFrames: number;
}> = ({shot, fadeInFrames}) => {
  const frame = useCurrentFrame();
  const outFade = shot.transition_to_next.type === "fade"
    ? Math.max(0, shot.transition_to_next.duration_frames)
    : 0;
  const inFade = fadeInFrames;
  const opacityIn = inFade > 0
    ? interpolate(frame, [0, inFade], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.quad)
    })
    : 1;
  const opacityOut = outFade > 0
    ? interpolate(frame, [shot.duration_frames - outFade, shot.duration_frames], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.in(Easing.quad)
    })
    : 1;
  const opacity = Math.min(opacityIn, opacityOut);

  const subtleZoom = shot.extend_mode === "freeze_last_subtle_zoom"
    ? interpolate(frame, [0, shot.duration_frames], [1, 1.03], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp"
    })
    : 1;

  const src = shot.video_src.startsWith("/")
    ? `file://${shot.video_src}`
    : staticFile(shot.video_src);

  return (
    <AbsoluteFill style={{opacity}}>
      <AbsoluteFill style={{transform: `scale(${subtleZoom})`}}>
        <OffthreadVideo src={src} style={{width: "100%", height: "100%", objectFit: "cover"}} />
      </AbsoluteFill>
      {shot.subtitle_lines.length > 0 && (
        <div style={subtitleBaseStyle}>
          {shot.subtitle_lines.map((line, idx) => (
            <div key={`${shot.shot_id}-line-${idx}`} style={lineStyle}>
              {line}
            </div>
          ))}
        </div>
      )}
    </AbsoluteFill>
  );
};

export const VideoComposition: React.FC<VideoCompositionProps> = ({shots}) => {
  return (
    <AbsoluteFill style={{backgroundColor: "black"}}>
      {shots.map((shot, idx) => {
        const prev = idx > 0 ? shots[idx - 1] : null;
        const fadeInFrames = prev?.transition_to_next.type === "fade"
          ? Math.max(0, prev.transition_to_next.duration_frames)
          : 0;
        return (
          <Sequence
            key={shot.shot_id}
            from={shot.start_frame}
            durationInFrames={shot.duration_frames}
          >
            <ShotLayer shot={shot} fadeInFrames={fadeInFrames} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
