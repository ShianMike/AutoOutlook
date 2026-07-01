# Requirements Document

## Introduction

AutoOutlook is a severe weather outlook application with a React/TypeScript frontend and a Python machine-learning backend. The application already displays the SPC categorical outlook (NONE/TSTM/MRGL/SLGT/ENH/MDT/HIGH) alongside AutoOutlook's own generated categorical outlook, and it maintains a Risk Archive (the ENH+ verification archive) of past significant-risk days.

This feature adds display of the official SPC hazard outlook (tornado, hail, wind, and thunder probabilities) in the same style the application already uses for the SPC categorical outlook, and extends the Risk Archive so archived events can show the SPC hazard outlook alongside the SPC categorical outlook. It also captures four defects for correction: a caching defect that corrupts the current categorical and hazard outlooks after a page refresh, an incorrect ordering of Risk Archive issued dates, a broken "our model" (SPC backing) comparison on the Risk Archive, and a Risk Timeline that is out of sync in the merged outlook view and momentarily displays the wrong risk category when a period is clicked.

## Glossary

- **AutoOutlook**: The overall severe-weather outlook application, comprising the React/TypeScript frontend and the Python ML backend.
- **SPC**: The NOAA Storm Prediction Center, the authoritative external source of official convective outlooks.
- **SPC_Categorical_Outlook**: The official SPC categorical risk outlook expressed as risk-level polygons (NONE, TSTM, MRGL, SLGT, ENH, MDT, HIGH).
- **SPC_Hazard_Outlook**: The official SPC probabilistic hazard outlook expressed as probability shapes for the tornado, hail, wind, and thunder hazards, including significant-severe (hatched) areas where issued by SPC.
- **Hazard_Type**: One of the four hazard categories displayed: tornado, hail, wind, or thunder.
- **Generated_Hazard_Outlook**: AutoOutlook's own model-generated hazard probability outlook.
- **Hazard_Outlook_View**: The frontend view that renders hazard probability maps for each Hazard_Type.
- **Outlook_Map_Panel**: The frontend panel component (`OutlookMapPanel`) that hosts the categorical and hazard outlook maps and their controls.
- **Risk_Archive**: The ENH+ verification archive of past days that reached at least the ENH risk level, presented in `HistoricalEnhPlusVerification`.
- **Archive_Event**: A single dated entry in the Risk_Archive.
- **Issued_Date**: The event date associated with an Archive_Event, used to order Archive_Events.
- **Archive_Data_Service**: The frontend hook (`useEnhPlusArchiveEvents`) that loads Archive_Event data from the backend archive endpoints.
- **Archive_API**: The backend endpoints under `/api/outlook/enh-plus-archive-*` that serve archived outlook artifacts.
- **SPC_Backing_Mode**: The user-selectable comparison mode determining whether the displayed AutoOutlook outlook is the pure model ("Our Model") or the SPC blend ("SPC Blend").
- **Merged_Outlook**: The multi-cycle merged Day 1 or Day 2 outlook produced by element-wise maximum across cycles.
- **Cached_Outlook**: A previously fetched outlook (categorical and hazard) held in the frontend artifact cache to avoid refetching.
- **Outlook_Cache**: The in-memory frontend cache of probability tiles and risk polygons keyed by cycle and forecast hour (`useOutlookArtifacts`).
- **Risk_Timeline**: The period-by-period severe outlook component (`RiskTimeline`) that summarizes risk by forecast-hour window.
- **Timeline_Period**: A single forecast-hour window segment displayed in the Risk_Timeline.
- **Selected_Forecast_Hour**: The forecast hour currently selected by the user for display.

## Requirements

### Requirement 1: Display the SPC hazard outlook

**User Story:** As a forecaster, I want to see the official SPC hazard outlook (tornado, hail, wind, thunder), so that I can review SPC's probabilistic hazard guidance the same way I already review the SPC categorical outlook.

#### Acceptance Criteria

1. WHERE the SPC_Hazard_Outlook is available for the selected outlook, THE Hazard_Outlook_View SHALL render, within 3 seconds of the outlook being selected, the SPC_Hazard_Outlook probability shapes for each of the four Hazard_Type values (tornado, hail, wind, thunder) that contain at least one probability area.
2. WHEN a user selects a Hazard_Type in the Hazard_Outlook_View, THE Hazard_Outlook_View SHALL, within 1 second of the selection, display only the SPC_Hazard_Outlook probability shapes for the selected Hazard_Type and hide the probability shapes for all other Hazard_Type values.
3. THE Hazard_Outlook_View SHALL render the SPC_Hazard_Outlook using the same map projection, geographic base layers, and risk-shape styling conventions used by the Outlook_Map_Panel to render the SPC_Categorical_Outlook.
4. WHERE the SPC_Hazard_Outlook includes a significant-severe area for the selected Hazard_Type, THE Hazard_Outlook_View SHALL render that significant-severe area as a hatched region whose fill pattern is visually distinct from all probability-threshold fills shown for that Hazard_Type.
4a. WHEN rendering a significant-severe area, THE Hazard_Outlook_View SHALL validate that the hatched fill pattern is visually distinct from the probability-threshold fills for that Hazard_Type, and IF the required visual distinctness cannot be achieved, THEN THE Hazard_Outlook_View SHALL NOT render that significant-severe area.
5. WHERE both the SPC_Hazard_Outlook and the Generated_Hazard_Outlook are available for the selected Hazard_Type, THE Hazard_Outlook_View SHALL allow the user to toggle the SPC_Hazard_Outlook on or off as an overlay layer displayed concurrently with the Generated_Hazard_Outlook, with both layers using the projection and base layers defined in criterion 3.
6. IF the SPC_Hazard_Outlook is unavailable for the selected outlook, THEN THE Hazard_Outlook_View SHALL display a visible message identifying the SPC hazard outlook as unavailable, SHALL continue to display the Generated_Hazard_Outlook, and SHALL retain the currently selected Hazard_Type.
7. WHERE the SPC_Hazard_Outlook is rendered for the selected Hazard_Type, THE Hazard_Outlook_View SHALL display a legend that lists each SPC hazard probability threshold present in the rendered outlook, with each legend entry paired with the shape fill styling used for that threshold, and SHALL include a distinct entry for the significant-severe hatched region when that region is shown.

### Requirement 2: Serve the SPC hazard outlook from the backend

**User Story:** As a frontend developer, I want the backend to provide the SPC hazard outlook data, so that the frontend can render it consistently with existing outlook data.

#### Acceptance Criteria

1. WHEN the frontend requests the SPC_Hazard_Outlook for the current outlook, THE AutoOutlook backend SHALL return, within 5 seconds, the SPC_Hazard_Outlook containing zero or more probability shapes for each of the tornado, hail, wind, and thunder Hazard_Types applicable to that outlook.
2. THE AutoOutlook backend SHALL include, for each returned SPC hazard probability shape, the Hazard_Type (exactly one of tornado, hail, wind, or thunder), the probability threshold expressed as a percentage value between 0 and 100 inclusive, and a boolean value indicating whether the shape is a significant-severe area.
3. WHEN a Hazard_Type has no probability shapes for the requested outlook, THE AutoOutlook backend SHALL return an empty shape collection for that Hazard_Type in a successful response rather than omitting the Hazard_Type.
4. IF no SPC_Hazard_Outlook exists for the requested outlook, THEN THE AutoOutlook backend SHALL respond with a not-found status that is distinct from both a successful response and a server-error response, and the response SHALL contain no probability shapes.
5. IF the backend cannot retrieve or produce the SPC_Hazard_Outlook due to a data-source or processing failure, THEN THE AutoOutlook backend SHALL respond with a server-error status that is distinguishable from the not-found status of criterion 4, and SHALL NOT return partial probability shapes.

### Requirement 3: Add the SPC hazard outlook to the Risk Archive

**User Story:** As a forecaster reviewing past significant-risk days, I want archived events to show the SPC hazard outlook alongside the SPC categorical outlook, so that I can verify SPC hazard guidance for historical events.

#### Acceptance Criteria

1. WHEN an Archive_Event is displayed, THE Risk_Archive SHALL make the archived SPC_Hazard_Outlook for that Archive_Event available to the Hazard_Outlook_View within 2 seconds of the Archive_Event being displayed.
2. WHEN a user views the Hazard_Outlook_View for an Archive_Event, THE Risk_Archive SHALL display the archived SPC_Hazard_Outlook for the selected Hazard_Type within 2 seconds of the Hazard_Type selection.
3. WHERE no valid Hazard_Type is currently selected for an Archive_Event — whether because no selection has been made or because a previous selection is no longer valid — THE Risk_Archive SHALL display the archived SPC_Hazard_Outlook for a defined default Hazard_Type.
4. WHEN the Archive_Data_Service loads an Archive_Event, THE Archive_Data_Service SHALL request the archived SPC_Hazard_Outlook from the Archive_API for that event's Issued_Date, applying a request timeout of 10 seconds and up to 2 retries before treating the outlook as unavailable.
5. WHEN a new Archive_Event is recorded, THE AutoOutlook backend SHALL store one SPC_Hazard_Outlook per supported Hazard_Type for that event so that each can be served through the Archive_API.
6. IF the archived SPC_Hazard_Outlook is unavailable for an Archive_Event — due to a request failure, the request timeout being exceeded, or an empty result after all retries — THEN THE Risk_Archive SHALL display an indication that the SPC hazard outlook is unavailable for that event, SHALL continue to display the archived SPC_Categorical_Outlook, and SHALL preserve the currently selected Hazard_Type.
7. IF the archived SPC_Hazard_Outlook is unavailable for a single Hazard_Type but available for other Hazard_Types of the same Archive_Event, THEN THE Risk_Archive SHALL display the available Hazard_Types and SHALL indicate unavailability only for the affected Hazard_Type.
8. THE Risk_Archive SHALL render the archived SPC_Hazard_Outlook using the same probability-threshold color mapping, legend, and overlay layout used for the live SPC_Hazard_Outlook.

### Requirement 4: Preserve cached outlooks across page refresh

**User Story:** As a user, I want cached Day 1 and Day 2 outlooks to remain intact after refreshing the site, so that the current categorical and hazard outlooks stay correct.

#### Acceptance Criteria

1. WHEN the site is refreshed, THE Outlook_Cache SHALL retain each Cached_Outlook whose cycle identity — defined as the combination of issuing day, cycle timestamp, and outlook type (Day 1 or Day 2) — matches the cycle identity of the newly loaded outlook.
2. WHILE a newly loaded outlook shares the same cycle identity as a Cached_Outlook, THE Outlook_Cache SHALL reuse the Cached_Outlook and SHALL reject any replacement that is empty or that is missing required fields present in the retained Cached_Outlook.
3. WHEN the site is refreshed, THE AutoOutlook frontend SHALL display the current SPC_Categorical_Outlook and the current SPC_Hazard_Outlook such that every displayed geometry and category value is identical to the corresponding value in the retained Cached_Outlook.
4. IF a newly loaded outlook has a different cycle identity than a Cached_Outlook, THEN THE Outlook_Cache SHALL replace the Cached_Outlook with the newly loaded outlook data.
5. WHILE a Merged_Outlook for Day 1 or Day 2 is being reloaded after refresh, THE AutoOutlook frontend SHALL continue to display the last valid Merged_Outlook until the reloaded Merged_Outlook is ready.
6. IF an outlook reload does not complete within 30 seconds of the refresh, THEN THE AutoOutlook frontend SHALL treat the reload as failed.
7. IF an outlook reload fails after a refresh, THEN THE AutoOutlook frontend SHALL retain the last valid displayed outlook and SHALL display a status message identifying the affected outlook day and describing the failed reload.
8. IF no Cached_Outlook exists for the newly loaded outlook after a refresh, THEN THE Outlook_Cache SHALL store the newly loaded outlook as the Cached_Outlook for its cycle identity.

### Requirement 5: Order Risk Archive events by issued date

**User Story:** As a user browsing the Risk Archive, I want events ordered consistently by issued date with the newest first, so that I can find recent events quickly.

#### Acceptance Criteria

1. WHEN the Risk_Archive assembles Archive_Events for display, THE Risk_Archive SHALL sort all displayed Archive_Events by Issued_Date in descending order such that the Archive_Event with the most recent Issued_Date appears at position 1 and each subsequent Archive_Event has an Issued_Date less than or equal to the Archive_Event before it.
2. WHEN live Archive_Events and catalog Archive_Events are combined for display, THE Risk_Archive SHALL merge both sources into a single set before sorting and SHALL apply the descending Issued_Date ordering across the entire combined set of Archive_Events.
3. WHEN two or more Archive_Events resolve to the same Issued_Date value, THE Risk_Archive SHALL display exactly one Archive_Event for that Issued_Date and SHALL exclude all other Archive_Events sharing that same Issued_Date from the display.
4. WHERE a live Archive_Event and a catalog Archive_Event share the same Issued_Date value, THE Risk_Archive SHALL retain the live Archive_Event for display and SHALL discard the catalog Archive_Event.
5. IF an Archive_Event has a missing, null, or unparseable Issued_Date, THEN THE Risk_Archive SHALL exclude that Archive_Event from the displayed and sorted set and SHALL retain the remaining valid Archive_Events in descending Issued_Date order.

### Requirement 6: Support the SPC backing comparison on the Risk Archive

**User Story:** As a user reviewing an archived event, I want the "Our Model" versus "SPC Blend" comparison to work, so that I can compare AutoOutlook's pure model outlook against the SPC-blended outlook for that event.

#### Acceptance Criteria

1. WHEN a user views an Archive_Event, THE Risk_Archive SHALL display a control that allows the user to select between the "Our Model" and "SPC Blend" values of the SPC_Backing_Mode.
2. WHEN the user selects the "Our Model" SPC_Backing_Mode for an Archive_Event, THE Risk_Archive SHALL display the pure AutoOutlook outlook for that Archive_Event within 2 seconds.
3. WHEN the user selects the "SPC Blend" SPC_Backing_Mode for an Archive_Event, THE Risk_Archive SHALL display the SPC-blended AutoOutlook outlook for that Archive_Event within 2 seconds.
4. WHEN the user changes the SPC_Backing_Mode for an Archive_Event, THE Risk_Archive SHALL replace the currently displayed outlook with the outlook corresponding to the newly selected SPC_Backing_Mode within 2 seconds, without navigating away from the currently displayed Archive_Event, and IF the newly selected SPC_Backing_Mode's data is unavailable during the switch, THEN THE Risk_Archive SHALL still replace the previously displayed outlook with whatever data is available while displaying the unavailable message defined in criterion 5.
5. IF the outlook data for a selected SPC_Backing_Mode is unavailable for an Archive_Event, THEN THE Risk_Archive SHALL display a message indicating that the selected comparison is unavailable for that Archive_Event and SHALL clear the previously displayed outlook rather than continue displaying it.
6. WHEN a user first views an Archive_Event, THE Risk_Archive SHALL default the SPC_Backing_Mode selection to "Our Model".

### Requirement 7: Synchronize the Risk Timeline in the merged outlook view

**User Story:** As a user viewing the merged outlook, I want the Risk Timeline to stay synchronized with the displayed outlook, so that the timeline periods reflect the outlook I am viewing.

#### Acceptance Criteria

1. WHILE the Merged_Outlook is displayed, THE Risk_Timeline SHALL derive each Timeline_Period's risk category from the Merged_Outlook data currently displayed within 500 milliseconds of the Merged_Outlook being rendered, and WHEN a Timeline_Period resolves to a valid risk category that differs from its previously displayed category, THE Risk_Timeline SHALL update that Timeline_Period to the newly resolved category.
2. WHILE the Merged_Outlook is displayed, THE Risk_Timeline SHALL indicate exactly one Timeline_Period as corresponding to the currently displayed Merged_Outlook using a visual highlight distinct from all non-corresponding Timeline_Periods.
3. IF the Merged_Outlook data for a given Timeline_Period is unavailable or cannot be resolved to a risk category, THEN THE Risk_Timeline SHALL display that Timeline_Period with a "no data" indication distinct from all valid risk categories and SHALL retain the previously displayed risk categories for all other Timeline_Periods.
4. WHEN a user clicks a Timeline_Period, THE Risk_Timeline SHALL set the Selected_Forecast_Hour to that period's representative forecast hour exactly once per click.
5. WHEN a user clicks a Timeline_Period, THE Risk_Timeline SHALL display the risk category for the resulting Selected_Forecast_Hour within 500 milliseconds of the click and without displaying an intermediate risk category that differs from both the previously displayed category and the resulting category.
6. WHEN the Selected_Forecast_Hour changes, THE Risk_Timeline SHALL update the highlighted Timeline_Period to the single period containing the new Selected_Forecast_Hour within 500 milliseconds of the change.
