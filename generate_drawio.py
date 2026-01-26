#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate draw.io swimlane timeline diagrams from project management JSON data.

Usage:
    python generate_drawio.py project.json -o output.drawio
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any
import html


# Status color mapping
STATUS_COLORS = {
    "on_track": "#4CAF50",
    "complete": "#4CAF50",
    "in_progress": "#2196F3",
    "at_risk": "#FFC107",
    "delayed": "#F44336",
    "blocked": "#F44336",
    "not_started": "#9E9E9E",
}

# Risk severity colors
SEVERITY_COLORS = {
    "high": "#F44336",
    "medium": "#FFC107",
    "low": "#4CAF50",
}

# Layout constants
SWIMLANE_HEADER_WIDTH = 120
SWIMLANE_MIN_HEIGHT = 60
TIMELINE_HEADER_HEIGHT = 50
PIXELS_PER_DAY = 8
TASK_HEIGHT = 30
TASK_VERTICAL_PADDING = 8  # Padding between stacked tasks
TASK_TOP_PADDING = 15  # Padding from top of swimlane
MILESTONE_SIZE = 20
RISK_MARKER_SIZE = 12
TABLE_ROW_HEIGHT = 30
TABLE_START_Y_OFFSET = 80


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def date_to_x(date: datetime, project_start: datetime) -> float:
    """Convert a date to X coordinate."""
    days = (date - project_start).days
    return SWIMLANE_HEADER_WIDTH + (days * PIXELS_PER_DAY)


def escape_xml(text: str) -> str:
    """Escape text for XML attributes."""
    return html.escape(str(text), quote=True)


_id_counter = 0

def generate_id() -> str:
    """Generate a unique ID for mxCell elements."""
    global _id_counter
    _id_counter += 1
    return f"cell_{_id_counter}"


class DrawioGenerator:
    """Generate draw.io XML from project data."""

    def __init__(self, data: dict):
        self.data = data
        self.project = data["project"]
        self.workstreams = {ws["id"]: ws for ws in data.get("workstreams", [])}
        self.tasks = {t["id"]: t for t in data.get("tasks", [])}
        self.milestones = {m["id"]: m for m in data.get("milestones", [])}
        self.dependencies = data.get("dependencies", [])
        self.risks = data.get("risks", [])
        self.date_markers = data.get("date_markers", [])

        # Parse project dates
        self.project_start = parse_date(self.project["start_date"])
        self.project_end = parse_date(self.project["end_date"])

        # Build risk lookup by affected item
        self.risks_by_item = {}
        for risk in self.risks:
            for item_id in risk.get("affected", []):
                if item_id not in self.risks_by_item:
                    self.risks_by_item[item_id] = []
                self.risks_by_item[item_id].append(risk)

        # Track cell IDs for dependency connections
        self.cell_ids = {}
        self.cell_positions = {}

        # Calculate layout with overlap handling
        self.task_rows = {}  # task_id -> row number within workstream
        self.swimlane_heights = {}  # ws_id -> computed height
        self.swimlane_y_starts = {}  # ws_id -> Y coordinate where swimlane starts
        self._calculate_layout()

    def _tasks_overlap(self, task1: dict, task2: dict) -> bool:
        """Check if two tasks have overlapping date ranges."""
        start1 = parse_date(task1["start"])
        end1 = parse_date(task1["end"])
        start2 = parse_date(task2["start"])
        end2 = parse_date(task2["end"])
        # Tasks overlap if one starts before the other ends
        return start1 < end2 and start2 < end1

    def _calculate_layout(self):
        """Calculate task row assignments and swimlane heights to handle overlaps."""
        ws_list = list(self.workstreams.keys())

        # Group tasks by workstream
        tasks_by_ws = {ws_id: [] for ws_id in ws_list}
        for task_id, task in self.tasks.items():
            ws_id = task.get("workstream")
            if ws_id in tasks_by_ws:
                tasks_by_ws[ws_id].append((task_id, task))

        # For each workstream, assign tasks to rows
        for ws_id in ws_list:
            ws_tasks = tasks_by_ws[ws_id]
            # Sort tasks by start date
            ws_tasks.sort(key=lambda t: parse_date(t[1]["start"]))

            # Rows are lists of tasks assigned to that row
            rows = []

            for task_id, task in ws_tasks:
                # Find the first row where this task doesn't overlap with existing tasks
                assigned = False
                for row_idx, row_tasks in enumerate(rows):
                    overlaps = False
                    for existing_task in row_tasks:
                        if self._tasks_overlap(task, existing_task):
                            overlaps = True
                            break
                    if not overlaps:
                        rows[row_idx].append(task)
                        self.task_rows[task_id] = row_idx
                        assigned = True
                        break

                if not assigned:
                    # Create a new row
                    rows.append([task])
                    self.task_rows[task_id] = len(rows) - 1

            # Calculate swimlane height based on number of rows
            num_rows = max(len(rows), 1)
            height = TASK_TOP_PADDING * 2 + num_rows * TASK_HEIGHT + (num_rows - 1) * TASK_VERTICAL_PADDING
            self.swimlane_heights[ws_id] = max(height, SWIMLANE_MIN_HEIGHT)

        # Calculate Y start positions for each swimlane
        current_y = TIMELINE_HEADER_HEIGHT
        for ws_id in ws_list:
            self.swimlane_y_starts[ws_id] = current_y
            current_y += self.swimlane_heights[ws_id]

    def get_workstream_y(self, ws_id: str) -> float:
        """Get Y coordinate for a workstream."""
        return self.swimlane_y_starts.get(ws_id, TIMELINE_HEADER_HEIGHT)

    def get_swimlane_height(self, ws_id: str) -> float:
        """Get the computed height for a workstream's swimlane."""
        return self.swimlane_heights.get(ws_id, SWIMLANE_MIN_HEIGHT)

    def get_task_y(self, ws_id: str, task_id: str) -> float:
        """Get Y coordinate for a task within a workstream, accounting for row stacking."""
        ws_y = self.get_workstream_y(ws_id)
        row = self.task_rows.get(task_id, 0)
        return ws_y + TASK_TOP_PADDING + row * (TASK_HEIGHT + TASK_VERTICAL_PADDING)

    def calculate_diagram_size(self) -> tuple[float, float]:
        """Calculate total diagram dimensions."""
        days = (self.project_end - self.project_start).days
        width = SWIMLANE_HEADER_WIDTH + (days * PIXELS_PER_DAY) + 50

        # Sum up all swimlane heights
        height = TIMELINE_HEADER_HEIGHT
        for ws_id in self.workstreams:
            height += self.get_swimlane_height(ws_id)

        # Add space for risk table
        if self.risks:
            height += TABLE_START_Y_OFFSET + TABLE_ROW_HEIGHT * (len(self.risks) + 1) + 50

        return width, height

    def create_mxfile(self) -> ET.Element:
        """Create the root mxfile element."""
        mxfile = ET.Element("mxfile")
        mxfile.set("host", "app.diagrams.net")
        mxfile.set("modified", datetime.now().isoformat())
        mxfile.set("agent", "Project Timeline Generator")
        mxfile.set("version", "21.0.0")
        mxfile.set("type", "device")
        return mxfile

    def create_diagram(self, mxfile: ET.Element) -> ET.Element:
        """Create the diagram element."""
        diagram = ET.SubElement(mxfile, "diagram")
        diagram.set("id", "project-timeline")
        diagram.set("name", escape_xml(self.project.get("name", "Project Timeline")))
        return diagram

    def create_graph_model(self, diagram: ET.Element) -> ET.Element:
        """Create the mxGraphModel element."""
        width, height = self.calculate_diagram_size()

        model = ET.SubElement(diagram, "mxGraphModel")
        model.set("dx", "0")
        model.set("dy", "0")
        model.set("grid", "1")
        model.set("gridSize", "10")
        model.set("guides", "1")
        model.set("tooltips", "1")
        model.set("connect", "1")
        model.set("arrows", "1")
        model.set("fold", "1")
        model.set("page", "1")
        model.set("pageScale", "1")
        model.set("pageWidth", str(int(width)))
        model.set("pageHeight", str(int(height)))
        model.set("math", "0")
        model.set("shadow", "0")

        return model

    def add_root_cells(self, root: ET.Element):
        """Add the required root cells (0 and 1)."""
        cell0 = ET.SubElement(root, "mxCell")
        cell0.set("id", "0")

        cell1 = ET.SubElement(root, "mxCell")
        cell1.set("id", "1")
        cell1.set("parent", "0")

    def add_weekend_shading(self, root: ET.Element):
        """Add grey shading for weekend days (Saturday and Sunday)."""
        show_weekends = self.project.get("show_weekends", True)
        if not show_weekends:
            return

        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)

        # Iterate through each day in the project range
        current = self.project_start
        while current <= self.project_end:
            # Check if it's Saturday (5) or Sunday (6)
            if current.weekday() in (5, 6):
                x = date_to_x(current, self.project_start)

                # Weekend shading rectangle
                weekend_cell = ET.SubElement(root, "mxCell")
                weekend_cell.set("id", generate_id())
                weekend_cell.set("value", "")
                weekend_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E8E8E8;strokeColor=none;opacity=50;")
                weekend_cell.set("vertex", "1")
                weekend_cell.set("parent", "1")

                geom = ET.SubElement(weekend_cell, "mxGeometry")
                geom.set("x", str(int(x)))
                geom.set("y", str(TIMELINE_HEADER_HEIGHT))
                geom.set("width", str(PIXELS_PER_DAY))
                geom.set("height", str(int(total_swimlane_height)))
                geom.set("as", "geometry")

            current += timedelta(days=1)

    def add_timeline_header(self, root: ET.Element):
        """Add timeline header with week/month markers aligned to Mondays."""
        width, _ = self.calculate_diagram_size()
        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)

        # Header background
        header_cell = ET.SubElement(root, "mxCell")
        header_id = generate_id()
        header_cell.set("id", header_id)
        header_cell.set("value", "")
        header_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;")
        header_cell.set("vertex", "1")
        header_cell.set("parent", "1")

        geom = ET.SubElement(header_cell, "mxGeometry")
        geom.set("x", str(SWIMLANE_HEADER_WIDTH))
        geom.set("y", "0")
        geom.set("width", str(int(width - SWIMLANE_HEADER_WIDTH)))
        geom.set("height", str(TIMELINE_HEADER_HEIGHT))
        geom.set("as", "geometry")

        time_unit = self.project.get("time_unit", "week")

        # Find the first Monday on or after project start
        current = self.project_start
        days_until_monday = (7 - current.weekday()) % 7
        if days_until_monday > 0:
            first_monday = current + timedelta(days=days_until_monday)
        else:
            first_monday = current  # Already a Monday

        # Add week markers aligned to Mondays
        week_num = 1
        current = first_monday

        while current <= self.project_end + timedelta(days=7):
            x = date_to_x(current, self.project_start)

            # Only draw if within visible area
            if x >= SWIMLANE_HEADER_WIDTH:
                # Monday vertical line (solid, darker)
                line_cell = ET.SubElement(root, "mxCell")
                line_cell.set("id", generate_id())
                line_cell.set("value", "")
                line_cell.set("style", "endArrow=none;html=1;strokeColor=#999999;strokeWidth=1;")
                line_cell.set("edge", "1")
                line_cell.set("parent", "1")

                line_geom = ET.SubElement(line_cell, "mxGeometry")
                line_geom.set("relative", "1")
                line_geom.set("as", "geometry")

                source = ET.SubElement(line_geom, "mxPoint")
                source.set("x", str(int(x)))
                source.set("y", str(TIMELINE_HEADER_HEIGHT))
                source.set("as", "sourcePoint")

                target = ET.SubElement(line_geom, "mxPoint")
                target.set("x", str(int(x)))
                target.set("y", str(int(TIMELINE_HEADER_HEIGHT + total_swimlane_height)))
                target.set("as", "targetPoint")

                # Week label with date
                label = f"W{week_num}: {current.strftime('%b %d')}"

                label_cell = ET.SubElement(root, "mxCell")
                label_cell.set("id", generate_id())
                label_cell.set("value", escape_xml(label))
                label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=9;fontStyle=1;")
                label_cell.set("vertex", "1")
                label_cell.set("parent", "1")

                label_geom = ET.SubElement(label_cell, "mxGeometry")
                label_geom.set("x", str(int(x + 2)))
                label_geom.set("y", "5")
                label_geom.set("width", str(int(PIXELS_PER_DAY * 5)))
                label_geom.set("height", "15")
                label_geom.set("as", "geometry")

                # Add day labels (M T W T F) below week label
                day_labels = ["M", "T", "W", "T", "F"]
                for day_idx, day_label in enumerate(day_labels):
                    day_x = x + (day_idx * PIXELS_PER_DAY)

                    day_cell = ET.SubElement(root, "mxCell")
                    day_cell.set("id", generate_id())
                    day_cell.set("value", day_label)
                    day_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=8;fontColor=#666666;")
                    day_cell.set("vertex", "1")
                    day_cell.set("parent", "1")

                    day_geom = ET.SubElement(day_cell, "mxGeometry")
                    day_geom.set("x", str(int(day_x)))
                    day_geom.set("y", "25")
                    day_geom.set("width", str(PIXELS_PER_DAY))
                    day_geom.set("height", "15")
                    day_geom.set("as", "geometry")

                    # Light vertical gridline for each weekday
                    if day_idx > 0:  # Skip Monday, already has a line
                        day_line = ET.SubElement(root, "mxCell")
                        day_line.set("id", generate_id())
                        day_line.set("value", "")
                        day_line.set("style", "endArrow=none;html=1;strokeColor=#E0E0E0;strokeWidth=1;")
                        day_line.set("edge", "1")
                        day_line.set("parent", "1")

                        day_line_geom = ET.SubElement(day_line, "mxGeometry")
                        day_line_geom.set("relative", "1")
                        day_line_geom.set("as", "geometry")

                        day_source = ET.SubElement(day_line_geom, "mxPoint")
                        day_source.set("x", str(int(day_x)))
                        day_source.set("y", str(TIMELINE_HEADER_HEIGHT))
                        day_source.set("as", "sourcePoint")

                        day_target = ET.SubElement(day_line_geom, "mxPoint")
                        day_target.set("x", str(int(day_x)))
                        day_target.set("y", str(int(TIMELINE_HEADER_HEIGHT + total_swimlane_height)))
                        day_target.set("as", "targetPoint")

            week_num += 1
            current += timedelta(weeks=1)

    def add_swimlanes(self, root: ET.Element):
        """Add swimlane containers for each workstream."""
        width, _ = self.calculate_diagram_size()

        for ws_id in self.workstreams:
            ws = self.workstreams[ws_id]
            y = self.get_workstream_y(ws_id)
            height = self.get_swimlane_height(ws_id)
            color = ws.get("color", "#E3F2FD")

            # Swimlane header
            header_cell = ET.SubElement(root, "mxCell")
            header_id = f"swimlane_{ws_id}"
            header_cell.set("id", header_id)
            header_cell.set("value", escape_xml(ws["name"]))
            header_cell.set("style", f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#666666;fontStyle=1;fontSize=12;verticalAlign=middle;")
            header_cell.set("vertex", "1")
            header_cell.set("parent", "1")

            geom = ET.SubElement(header_cell, "mxGeometry")
            geom.set("x", "0")
            geom.set("y", str(int(y)))
            geom.set("width", str(SWIMLANE_HEADER_WIDTH))
            geom.set("height", str(int(height)))
            geom.set("as", "geometry")

            # Swimlane row background
            row_cell = ET.SubElement(root, "mxCell")
            row_cell.set("id", generate_id())
            row_cell.set("value", "")
            # Lighter version of the color
            row_cell.set("style", f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};fillOpacity=20;strokeColor=#CCCCCC;")
            row_cell.set("vertex", "1")
            row_cell.set("parent", "1")

            row_geom = ET.SubElement(row_cell, "mxGeometry")
            row_geom.set("x", str(SWIMLANE_HEADER_WIDTH))
            row_geom.set("y", str(int(y)))
            row_geom.set("width", str(int(width - SWIMLANE_HEADER_WIDTH)))
            row_geom.set("height", str(int(height)))
            row_geom.set("as", "geometry")

    def add_date_markers(self, root: ET.Element):
        """Add vertical date marker lines for important dates."""
        if not self.date_markers:
            return

        # Calculate the vertical span for markers
        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        marker_top = TIMELINE_HEADER_HEIGHT
        marker_bottom = TIMELINE_HEADER_HEIGHT + total_swimlane_height

        for marker in self.date_markers:
            marker_date = parse_date(marker["date"])

            # Skip markers outside project range
            if marker_date < self.project_start or marker_date > self.project_end:
                continue

            x = date_to_x(marker_date, self.project_start)
            color = marker.get("color", "#FF0000")
            style = marker.get("style", "dashed")
            name = marker.get("name", "")

            # Determine stroke style
            if style == "dashed":
                stroke_style = "dashed=1;dashPattern=8 4;"
            elif style == "dotted":
                stroke_style = "dashed=1;dashPattern=2 2;"
            else:
                stroke_style = ""

            # Vertical marker line
            line_cell = ET.SubElement(root, "mxCell")
            line_cell.set("id", generate_id())
            line_cell.set("value", "")
            line_cell.set("style", f"endArrow=none;html=1;strokeColor={color};strokeWidth=2;{stroke_style}")
            line_cell.set("edge", "1")
            line_cell.set("parent", "1")

            line_geom = ET.SubElement(line_cell, "mxGeometry")
            line_geom.set("relative", "1")
            line_geom.set("as", "geometry")

            source = ET.SubElement(line_geom, "mxPoint")
            source.set("x", str(int(x)))
            source.set("y", str(int(marker_top)))
            source.set("as", "sourcePoint")

            target = ET.SubElement(line_geom, "mxPoint")
            target.set("x", str(int(x)))
            target.set("y", str(int(marker_bottom)))
            target.set("as", "targetPoint")

            # Marker label at top (rotated)
            if name:
                label_cell = ET.SubElement(root, "mxCell")
                label_cell.set("id", generate_id())
                label_cell.set("value", f"<b>{escape_xml(name)}</b>")
                label_cell.set("style", f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=bottom;whiteSpace=wrap;rounded=0;fontSize=9;fontColor={color};rotation=-90;")
                label_cell.set("vertex", "1")
                label_cell.set("parent", "1")

                label_geom = ET.SubElement(label_cell, "mxGeometry")
                label_geom.set("x", str(int(x - 8)))
                label_geom.set("y", str(int(marker_bottom - 100)))
                label_geom.set("width", str(100))
                label_geom.set("height", str(16))
                label_geom.set("as", "geometry")

    def add_tasks(self, root: ET.Element):
        """Add task bars to the diagram."""
        for task_id, task in self.tasks.items():
            ws_id = task["workstream"]
            if ws_id not in self.workstreams:
                continue

            start_date = parse_date(task["start"])
            end_date = parse_date(task["end"])

            x = date_to_x(start_date, self.project_start)
            y = self.get_task_y(ws_id, task_id)
            width = (end_date - start_date).days * PIXELS_PER_DAY

            status = task.get("status", "not_started")
            status_color = STATUS_COLORS.get(status, STATUS_COLORS["not_started"])
            percent = task.get("percent_complete", 0)

            # Store position for dependencies
            cell_id = f"task_{task_id}"
            self.cell_ids[task_id] = cell_id
            self.cell_positions[task_id] = {
                "x": x, "y": y, "width": width, "height": TASK_HEIGHT,
                "center_x": x + width / 2, "center_y": y + TASK_HEIGHT / 2,
                "right_x": x + width, "left_x": x
            }

            # Task container (background)
            task_cell = ET.SubElement(root, "mxCell")
            task_cell.set("id", cell_id)

            # Build label with name and owner
            owner = task.get("owner", "")
            label = f"<b>{escape_xml(task['name'])}</b>"
            if owner:
                label += f"<br><font style=\"font-size:10px\">{escape_xml(owner)}</font>"

            task_cell.set("value", label)
            task_cell.set("style", f"rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor={status_color};strokeWidth=2;verticalAlign=middle;fontSize=11;")
            task_cell.set("vertex", "1")
            task_cell.set("parent", "1")

            geom = ET.SubElement(task_cell, "mxGeometry")
            geom.set("x", str(int(x)))
            geom.set("y", str(int(y)))
            geom.set("width", str(int(max(width, 40))))
            geom.set("height", str(TASK_HEIGHT))
            geom.set("as", "geometry")

            # Progress fill overlay
            if percent > 0:
                progress_width = (width * percent / 100)
                progress_cell = ET.SubElement(root, "mxCell")
                progress_cell.set("id", generate_id())
                progress_cell.set("value", "")
                progress_cell.set("style", f"rounded=1;whiteSpace=wrap;html=1;fillColor={status_color};fillOpacity=30;strokeColor=none;")
                progress_cell.set("vertex", "1")
                progress_cell.set("parent", "1")

                prog_geom = ET.SubElement(progress_cell, "mxGeometry")
                prog_geom.set("x", str(int(x)))
                prog_geom.set("y", str(int(y)))
                prog_geom.set("width", str(int(max(progress_width, 5))))
                prog_geom.set("height", str(TASK_HEIGHT))
                prog_geom.set("as", "geometry")

            # Add risk markers if this task has associated risks
            self.add_risk_markers(root, task_id, x + width - 15, y - 5)

    def add_milestones(self, root: ET.Element):
        """Add milestone diamonds to the diagram."""
        for ms_id, milestone in self.milestones.items():
            ws_id = milestone["workstream"]
            if ws_id not in self.workstreams:
                continue

            ms_date = parse_date(milestone["date"])
            x = date_to_x(ms_date, self.project_start) - MILESTONE_SIZE / 2
            # Place milestones in the first row of their workstream
            ws_y = self.get_workstream_y(ws_id)
            y = ws_y + TASK_TOP_PADDING + (TASK_HEIGHT - MILESTONE_SIZE) / 2

            status = milestone.get("status", "on_track")
            status_color = STATUS_COLORS.get(status, STATUS_COLORS["on_track"])

            # Store position for dependencies
            cell_id = f"milestone_{ms_id}"
            self.cell_ids[ms_id] = cell_id
            self.cell_positions[ms_id] = {
                "x": x, "y": y, "width": MILESTONE_SIZE, "height": MILESTONE_SIZE,
                "center_x": x + MILESTONE_SIZE / 2, "center_y": y + MILESTONE_SIZE / 2,
                "right_x": x + MILESTONE_SIZE, "left_x": x
            }

            # Milestone diamond
            ms_cell = ET.SubElement(root, "mxCell")
            ms_cell.set("id", cell_id)
            ms_cell.set("value", "")
            ms_cell.set("style", f"rhombus;whiteSpace=wrap;html=1;fillColor={status_color};strokeColor=#333333;strokeWidth=2;")
            ms_cell.set("vertex", "1")
            ms_cell.set("parent", "1")

            geom = ET.SubElement(ms_cell, "mxGeometry")
            geom.set("x", str(int(x)))
            geom.set("y", str(int(y)))
            geom.set("width", str(MILESTONE_SIZE))
            geom.set("height", str(MILESTONE_SIZE))
            geom.set("as", "geometry")

            # Milestone label (below the diamond)
            label_cell = ET.SubElement(root, "mxCell")
            label_cell.set("id", generate_id())
            label_cell.set("value", f"<b>{escape_xml(milestone['name'])}</b>")
            label_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=top;whiteSpace=wrap;rounded=0;fontSize=9;")
            label_cell.set("vertex", "1")
            label_cell.set("parent", "1")

            label_geom = ET.SubElement(label_cell, "mxGeometry")
            label_geom.set("x", str(int(x - 30)))
            label_geom.set("y", str(int(y + MILESTONE_SIZE + 2)))
            label_geom.set("width", str(MILESTONE_SIZE + 60))
            label_geom.set("height", str(20))
            label_geom.set("as", "geometry")

            # Add risk markers if this milestone has associated risks
            self.add_risk_markers(root, ms_id, x + MILESTONE_SIZE + 2, y - 5)

    def add_risk_markers(self, root: ET.Element, item_id: str, x: float, y: float):
        """Add risk indicator circles on affected items."""
        if item_id not in self.risks_by_item:
            return

        risks = self.risks_by_item[item_id]
        # Find the highest severity risk
        highest_severity = "low"
        for risk in risks:
            sev = risk.get("severity", "low")
            if sev == "high":
                highest_severity = "high"
                break
            elif sev == "medium" and highest_severity == "low":
                highest_severity = "medium"

        color = SEVERITY_COLORS.get(highest_severity, SEVERITY_COLORS["low"])

        marker_cell = ET.SubElement(root, "mxCell")
        marker_cell.set("id", generate_id())
        marker_cell.set("value", "!")
        marker_cell.set("style", f"ellipse;whiteSpace=wrap;html=1;fillColor={color};strokeColor=#333333;fontColor=#FFFFFF;fontSize=8;fontStyle=1;")
        marker_cell.set("vertex", "1")
        marker_cell.set("parent", "1")

        geom = ET.SubElement(marker_cell, "mxGeometry")
        geom.set("x", str(int(x)))
        geom.set("y", str(int(y)))
        geom.set("width", str(RISK_MARKER_SIZE))
        geom.set("height", str(RISK_MARKER_SIZE))
        geom.set("as", "geometry")

    def add_dependencies(self, root: ET.Element):
        """Add dependency arrows between tasks/milestones."""
        for dep in self.dependencies:
            from_id = dep["from"]
            to_id = dep["to"]

            if from_id not in self.cell_positions or to_id not in self.cell_positions:
                continue

            from_pos = self.cell_positions[from_id]
            to_pos = self.cell_positions[to_id]

            # Create curved arrow from right edge of source to left edge of target
            edge_cell = ET.SubElement(root, "mxCell")
            edge_cell.set("id", generate_id())
            edge_cell.set("value", "")
            edge_cell.set("style", "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#666666;strokeWidth=1;endArrow=block;endFill=1;curved=1;")
            edge_cell.set("edge", "1")
            edge_cell.set("parent", "1")
            edge_cell.set("source", self.cell_ids.get(from_id, ""))
            edge_cell.set("target", self.cell_ids.get(to_id, ""))

            geom = ET.SubElement(edge_cell, "mxGeometry")
            geom.set("relative", "1")
            geom.set("as", "geometry")

    def add_risk_table(self, root: ET.Element):
        """Add risk summary table below the timeline."""
        if not self.risks:
            return

        # Calculate Y position after all swimlanes
        total_swimlane_height = sum(self.get_swimlane_height(ws_id) for ws_id in self.workstreams)
        table_y = TIMELINE_HEADER_HEIGHT + total_swimlane_height + TABLE_START_Y_OFFSET

        # Column widths
        col_widths = [60, 150, 80, 80, 80, 150, 250]
        headers = ["ID", "Risk Name", "Severity", "Likelihood", "Status", "Affected", "Mitigation"]

        total_width = sum(col_widths)

        # Table title
        title_cell = ET.SubElement(root, "mxCell")
        title_cell.set("id", generate_id())
        title_cell.set("value", "<b>Risk Summary</b>")
        title_cell.set("style", "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontSize=14;fontStyle=1;")
        title_cell.set("vertex", "1")
        title_cell.set("parent", "1")

        title_geom = ET.SubElement(title_cell, "mxGeometry")
        title_geom.set("x", "10")
        title_geom.set("y", str(int(table_y - 30)))
        title_geom.set("width", "200")
        title_geom.set("height", "25")
        title_geom.set("as", "geometry")

        # Header row
        x_offset = 10
        for i, header in enumerate(headers):
            header_cell = ET.SubElement(root, "mxCell")
            header_cell.set("id", generate_id())
            header_cell.set("value", f"<b>{header}</b>")
            header_cell.set("style", "rounded=0;whiteSpace=wrap;html=1;fillColor=#E0E0E0;strokeColor=#666666;fontSize=10;fontStyle=1;")
            header_cell.set("vertex", "1")
            header_cell.set("parent", "1")

            geom = ET.SubElement(header_cell, "mxGeometry")
            geom.set("x", str(x_offset))
            geom.set("y", str(int(table_y)))
            geom.set("width", str(col_widths[i]))
            geom.set("height", str(TABLE_ROW_HEIGHT))
            geom.set("as", "geometry")

            x_offset += col_widths[i]

        # Data rows
        for row_idx, risk in enumerate(self.risks):
            row_y = table_y + TABLE_ROW_HEIGHT * (row_idx + 1)
            x_offset = 10

            # Prepare affected items string
            affected = risk.get("affected", [])
            affected_names = []
            for item_id in affected:
                if item_id in self.tasks:
                    affected_names.append(self.tasks[item_id]["name"])
                elif item_id in self.milestones:
                    affected_names.append(self.milestones[item_id]["name"])
                else:
                    affected_names.append(item_id)
            affected_str = ", ".join(affected_names)

            row_data = [
                risk.get("id", ""),
                risk.get("name", ""),
                risk.get("severity", "").upper(),
                risk.get("likelihood", "").upper(),
                risk.get("status", "").replace("_", " ").title(),
                affected_str,
                risk.get("mitigation", "")
            ]

            severity = risk.get("severity", "low")
            severity_color = SEVERITY_COLORS.get(severity, "#FFFFFF")

            for col_idx, value in enumerate(row_data):
                cell = ET.SubElement(root, "mxCell")
                cell.set("id", generate_id())
                cell.set("value", escape_xml(str(value)))

                # Color the severity column
                if col_idx == 2:  # Severity column
                    style = f"rounded=0;whiteSpace=wrap;html=1;fillColor={severity_color};fillOpacity=30;strokeColor=#CCCCCC;fontSize=9;align=center;"
                else:
                    style = "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#CCCCCC;fontSize=9;align=left;spacingLeft=4;"

                cell.set("style", style)
                cell.set("vertex", "1")
                cell.set("parent", "1")

                geom = ET.SubElement(cell, "mxGeometry")
                geom.set("x", str(x_offset))
                geom.set("y", str(int(row_y)))
                geom.set("width", str(col_widths[col_idx]))
                geom.set("height", str(TABLE_ROW_HEIGHT))
                geom.set("as", "geometry")

                x_offset += col_widths[col_idx]

    def generate(self) -> str:
        """Generate the complete draw.io XML."""
        mxfile = self.create_mxfile()
        diagram = self.create_diagram(mxfile)
        model = self.create_graph_model(diagram)

        root = ET.SubElement(model, "root")

        self.add_root_cells(root)
        self.add_timeline_header(root)
        self.add_swimlanes(root)
        self.add_weekend_shading(root)
        self.add_date_markers(root)
        self.add_tasks(root)
        self.add_milestones(root)
        self.add_dependencies(root)
        self.add_risk_table(root)

        # Convert to string with XML declaration
        ET.indent(mxfile, space="  ")
        xml_str = ET.tostring(mxfile, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str


def load_project_data(filepath: str) -> dict:
    """Load and validate project JSON data."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Basic validation
    if "project" not in data:
        raise ValueError("Missing 'project' section in JSON")

    project = data["project"]
    required_fields = ["start_date", "end_date"]
    for field in required_fields:
        if field not in project:
            raise ValueError(f"Missing required field 'project.{field}'")

    # Validate dates
    try:
        parse_date(project["start_date"])
        parse_date(project["end_date"])
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}")

    return data


def main():
    parser = argparse.ArgumentParser(
        description="Generate draw.io swimlane timeline diagrams from project JSON data."
    )
    parser.add_argument(
        "input",
        help="Input project JSON file"
    )
    parser.add_argument(
        "-o", "--output",
        default="output.drawio",
        help="Output draw.io file (default: output.drawio)"
    )

    args = parser.parse_args()

    try:
        # Load project data
        print(f"Loading project data from {args.input}...")
        data = load_project_data(args.input)

        # Generate draw.io XML
        print("Generating draw.io diagram...")
        generator = DrawioGenerator(data)
        xml_output = generator.generate()

        # Write output file
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(xml_output)

        print(f"Successfully generated {args.output}")

        # Print summary
        num_tasks = len(data.get("tasks", []))
        num_milestones = len(data.get("milestones", []))
        num_workstreams = len(data.get("workstreams", []))
        num_risks = len(data.get("risks", []))

        num_markers = len(data.get("date_markers", []))

        print(f"\nDiagram contains:")
        print(f"  - {num_workstreams} workstreams")
        print(f"  - {num_tasks} tasks")
        print(f"  - {num_milestones} milestones")
        print(f"  - {len(data.get('dependencies', []))} dependencies")
        print(f"  - {num_markers} date markers")
        print(f"  - {num_risks} risks")

    except FileNotFoundError:
        print(f"Error: File '{args.input}' not found", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{args.input}': {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
