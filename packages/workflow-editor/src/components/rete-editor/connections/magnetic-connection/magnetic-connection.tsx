import type { ClassicScheme } from "rete-react-plugin";
import { Presets } from "rete-react-plugin";
import styled from "styled-components";

const { useConnection } = Presets.classic;

const Svg = styled.svg`
  overflow: visible !important;
  position: absolute;
  pointer-events: none;
  width: 9999px;
  height: 9999px;
`;

const Path = styled.path<{ styles?: (props: any) => any }>`
  fill: none;
  stroke-width: 2px;
  stroke: #ffd92c;
  pointer-events: auto;
  ${(props) => props.styles?.(props)};
  filter: blur(2px);
`;

export function MagneticConnection(props: {
    data: ClassicScheme["Connection"] & { isLoop?: boolean };
    styles?: () => any;
}) {
    const { path } = useConnection();

    if (!path) return null;

    return (
        <Svg data-id="connection">
            <Path styles={props.styles} d={path} />
        </Svg>
    );
}
