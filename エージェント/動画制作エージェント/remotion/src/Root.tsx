import React from "react";
import {Composition} from "remotion";
import {VideoComposition, type VideoCompositionProps} from "./VideoComposition";

const defaultProps: VideoCompositionProps = {
  fps: 24,
  width: 1920,
  height: 1080,
  total_frames: 240,
  shots: []
};

export const Root: React.FC = () => {
  return (
    <Composition<VideoCompositionProps>
      id="VideoPipelineComposition"
      component={VideoComposition}
      durationInFrames={defaultProps.total_frames}
      fps={defaultProps.fps}
      width={defaultProps.width}
      height={defaultProps.height}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => ({
        durationInFrames: props.total_frames,
        fps: props.fps,
        width: props.width,
        height: props.height
      })}
    />
  );
};
