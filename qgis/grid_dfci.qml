<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.44.7-Solothurn" styleCategories="Symbology|Labeling" labelsEnabled="1">
  <renderer-v2 type="RuleRenderer" symbollevels="0" forceraster="0" enableorderby="0" referencescale="-1">
    <rules key="{b0c1dfci-0001-4a11-8c22-dfci00000001}">
      <rule key="{b0c1dfci-100k-4a11-8c22-dfci00000001}" filter="&quot;niveau_m&quot; = 100000" symbol="0" label="DFCI 100 km" scalemindenom="1" scalemaxdenom="10000000"/>
      <rule key="{b0c1dfci-020k-4a11-8c22-dfci00000002}" filter="&quot;niveau_m&quot; = 20000" symbol="1" label="DFCI 20 km" scalemindenom="1" scalemaxdenom="750000"/>
      <rule key="{b0c1dfci-002k-4a11-8c22-dfci00000003}" filter="&quot;niveau_m&quot; = 2000" symbol="2" label="DFCI 2 km" scalemindenom="1" scalemaxdenom="120000"/>
    </rules>
    <symbols>
      <symbol type="fill" name="0" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option type="QString" name="color" value="0,0,0,0,rgb:0,0,0,0"/>
            <Option type="QString" name="style" value="no"/>
            <Option type="QString" name="outline_style" value="solid"/>
            <Option type="QString" name="outline_color" value="155,28,28,255,rgb:0.6078431,0.1098039,0.1098039,1"/>
            <Option type="QString" name="outline_width" value="0.9"/>
            <Option type="QString" name="outline_width_unit" value="MM"/>
            <Option type="QString" name="joinstyle" value="miter"/>
            <Option type="QString" name="offset" value="0,0"/>
            <Option type="QString" name="offset_unit" value="MM"/>
            <Option type="QString" name="offset_map_unit_scale" value="3x:0,0,0,0,0,0"/>
            <Option type="QString" name="border_width_map_unit_scale" value="3x:0,0,0,0,0,0"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="1" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option type="QString" name="color" value="0,0,0,0,rgb:0,0,0,0"/>
            <Option type="QString" name="style" value="no"/>
            <Option type="QString" name="outline_style" value="solid"/>
            <Option type="QString" name="outline_color" value="194,65,12,220,rgb:0.7607843,0.254902,0.0470588,0.86"/>
            <Option type="QString" name="outline_width" value="0.45"/>
            <Option type="QString" name="outline_width_unit" value="MM"/>
            <Option type="QString" name="joinstyle" value="miter"/>
            <Option type="QString" name="offset" value="0,0"/>
            <Option type="QString" name="offset_unit" value="MM"/>
            <Option type="QString" name="offset_map_unit_scale" value="3x:0,0,0,0,0,0"/>
            <Option type="QString" name="border_width_map_unit_scale" value="3x:0,0,0,0,0,0"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="fill" name="2" alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option type="QString" name="color" value="0,0,0,0,rgb:0,0,0,0"/>
            <Option type="QString" name="style" value="no"/>
            <Option type="QString" name="outline_style" value="solid"/>
            <Option type="QString" name="outline_color" value="202,138,4,180,rgb:0.7921569,0.5411765,0.0156863,0.7"/>
            <Option type="QString" name="outline_width" value="0.18"/>
            <Option type="QString" name="outline_width_unit" value="MM"/>
            <Option type="QString" name="joinstyle" value="miter"/>
            <Option type="QString" name="offset" value="0,0"/>
            <Option type="QString" name="offset_unit" value="MM"/>
            <Option type="QString" name="offset_map_unit_scale" value="3x:0,0,0,0,0,0"/>
            <Option type="QString" name="border_width_map_unit_scale" value="3x:0,0,0,0,0,0"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="DejaVu Sans" fontSize="8" fieldName="code" isExpression="0" textColor="120,20,20,255,rgb:0.47,0.08,0.08,1" namedStyle="" fontWeight="50" fontItalic="0" fontUnderline="0" fontStrikeout="0" multilineHeight="1" textOpacity="1" blendMode="0" capitalization="0" textOrientation="horizontal" fontSizeUnit="Point" allowHtml="0" forcedBold="0" forcedItalic="0" fontKerning="1" fontLetterSpacing="0" fontWordSpacing="0" previewBkgrdColor="255,255,255,255,rgb:1,1,1,1" legendString="Aa" fontSizeMapUnitScale="3x:0,0,0,0,0,0" tabStopDistance="6" tabStopDistanceUnit="Percentage" tabStopDistanceMapUnitScale="3x:0,0,0,0,0,0" multilineHeightUnit="Percentage">
        <families/>
        <text-buffer bufferDraw="1" bufferSize="0.8" bufferColor="255,255,255,220,rgb:1,1,1,0.86" bufferOpacity="1" bufferJoinStyle="64" bufferSizeUnits="MM" bufferSizeMapUnitScale="3x:0,0,0,0,0,0" bufferNoFill="0" bufferBlendMode="0"/>
        <text-mask maskEnabled="0" maskType="0" maskSize="0" maskSize2="0" maskSizeUnits="MM" maskJoinStyle="64" maskOpacity="1" maskSizeMapUnitScale="3x:0,0,0,0,0,0" maskedSymbolLayers=""/>
        <background shapeDraw="0" shapeType="0" shapeSVGFile="" shapeSizeX="0" shapeSizeY="0" shapeSizeType="0" shapeSizeUnit="MM" shapeSizeMapUnitScale="3x:0,0,0,0,0,0" shapeRotationType="0" shapeRotation="0" shapeOffsetX="0" shapeOffsetY="0" shapeOffsetUnit="MM" shapeOffsetMapUnitScale="3x:0,0,0,0,0,0" shapeRadiiX="0" shapeRadiiY="0" shapeRadiiUnit="MM" shapeRadiiMapUnitScale="3x:0,0,0,0,0,0" shapeFillColor="255,255,255,255,rgb:1,1,1,1" shapeBorderColor="128,128,128,255,rgb:0.5,0.5,0.5,1" shapeBorderWidth="0" shapeBorderWidthUnit="MM" shapeBorderWidthMapUnitScale="3x:0,0,0,0,0,0" shapeJoinStyle="64" shapeOpacity="1" shapeBlendMode="0"/>
        <shadow shadowDraw="0" shadowUnder="0" shadowOffsetAngle="135" shadowOffsetDist="1" shadowOffsetUnit="MM" shadowOffsetMapUnitScale="3x:0,0,0,0,0,0" shadowOffsetGlobal="1" shadowRadius="1.5" shadowRadiusUnit="MM" shadowRadiusMapUnitScale="3x:0,0,0,0,0,0" shadowRadiusAlphaOnly="0" shadowOpacity="0.7" shadowScale="100" shadowColor="0,0,0,255,rgb:0,0,0,1" shadowBlendMode="6"/>
        <dd_properties>
          <Option type="Map">
            <Option type="QString" name="name" value=""/>
            <Option name="properties"/>
            <Option type="QString" name="type" value="collection"/>
          </Option>
        </dd_properties>
        <substitutions/>
      </text-style>
      <text-format formatNumbers="0" decimals="3" plussign="0" wrapChar="" addDirectionSymbol="0" leftDirectionSymbol="&lt;" rightDirectionSymbol=">" reverseDirectionSymbol="0" placeDirectionSymbol="0" multilineAlign="1" autoWrapLength="0" useMaxLineLengthForAutoWrap="1"/>
      <placement placement="0" placementFlags="10" centroidWhole="1" centroidInside="1" fitInPolygonOnly="1" dist="0" distUnits="MM" distMapUnitScale="3x:0,0,0,0,0,0" offsetType="0" xOffset="0" yOffset="0" labelOffsetMapUnitScale="3x:0,0,0,0,0,0" quadOffset="4" rotationAngle="0" rotationUnit="AngleDegrees" preserveRotation="1" offsetUnits="MM" priority="4" repeatDistance="0" repeatDistanceUnits="MM" repeatDistanceMapUnitScale="3x:0,0,0,0,0,0" maxCurvedCharAngleIn="25" maxCurvedCharAngleOut="-25" overrunDistance="0" overrunDistanceUnit="MM" overrunDistanceMapUnitScale="3x:0,0,0,0,0,0" layerType="PolygonGeometry" geometryGeneratorEnabled="0" geometryGenerator="" geometryGeneratorType="PointGeometry" predefinedPositionOrder="TR,TL,BR,BL,R,L,TSR,BSR" lineAnchorType="0" lineAnchorClipping="0" lineAnchorPercent="0.5" lineAnchorTextPoint="FollowPlacement" maximumDistance="0" maximumDistanceUnit="MM" maximumDistanceMapUnitScale="3x:0,0,0,0,0,0" overlapHandling="PreventOverlap" allowDegraded="0" polygonPlacementFlags="2" prioritization="PreferCloser"/>
      <rendering drawLabels="1" scaleVisibility="1" scaleMin="1" scaleMax="250000" fontLimitPixelSize="0" fontMinPixelSize="0" fontMaxPixelSize="10000" obstacle="1" obstacleType="1" obstacleFactor="1" labelPerPart="0" mergeLines="0" minFeatureSize="0" limitNumLabels="1" maxNumLabels="400" upsidedownLabels="0" zIndex="0" unplacedVisibility="0"/>
      <dd_properties>
        <Option type="Map">
          <Option type="QString" name="name" value=""/>
          <Option name="properties"/>
          <Option type="QString" name="type" value="collection"/>
        </Option>
      </dd_properties>
      <callout type="simple">
        <Option type="Map">
          <Option type="QString" name="anchorPoint" value="pole_of_inaccessibility"/>
          <Option type="int" name="blendMode" value="0"/>
          <Option type="bool" name="drawToAllParts" value="false"/>
          <Option type="QString" name="enabled" value="0"/>
          <Option type="QString" name="labelAnchorPoint" value="point_on_exterior"/>
        </Option>
      </callout>
    </settings>
  </labeling>
  <layerGeometryType>2</layerGeometryType>
</qgis>
