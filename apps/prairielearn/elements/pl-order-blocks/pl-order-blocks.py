import base64
import json
import math
import os
import random
from copy import deepcopy
from enum import Enum
from typing import Any, Optional, TypedDict

import chevron
import lxml.html
import prairielearn as pl
from dag_checker import grade_dag, lcs_partial_credit, solve_dag
from lxml import etree
from typing_extensions import NotRequired, assert_never


class GradingMethodType(Enum):
    UNORDERED = "unordered"
    ORDERED = "ordered"
    RANKING = "ranking"
    DAG = "dag"
    EXTERNAL = "external"


class SourceBlocksOrderType(Enum):
    RANDOM = "random"
    ALPHABETIZED = "alphabetized"
    ORDERED = "ordered"


class FeedbackType(Enum):
    NONE = "none"
    FIRST_WRONG = "first-wrong"


class PartialCreditType(Enum):
    NONE = "none"
    LCS = "lcs"


class FormatType(Enum):
    DEFAULT = "default"
    CODE = "code"


class GroupInfo(TypedDict):
    tag: Optional[str]
    depends: Optional[list[str]]


class OrderBlocksAnswerData(TypedDict):
    inner_html: str
    indent: Optional[int]
    ranking: int
    index: int
    tag: str
    distractor_for: Optional[str]
    depends: list[str]  # only used with DAG grader
    group_info: GroupInfo  # only used with DAG grader
    distractor_bin: NotRequired[str]
    uuid: str


GRADING_METHOD_DEFAULT = GradingMethodType.ORDERED
SOURCE_BLOCKS_ORDER_DEFAULT = SourceBlocksOrderType.ALPHABETIZED
FEEDBACK_DEFAULT = FeedbackType.NONE
PL_ANSWER_CORRECT_DEFAULT = True
PL_ANSWER_INDENT_DEFAULT = -1
ALLOW_BLANK_DEFAULT = False
INDENTION_DEFAULT = False
MAX_INDENTION_DEFAULT = 4
SOURCE_HEADER_DEFAULT = "Drag from here:"
SOLUTION_HEADER_DEFAULT = "Construct your solution here:"
FILE_NAME_DEFAULT = "user_code.py"
SOLUTION_PLACEMENT_DEFAULT = "right"
WEIGHT_DEFAULT = 1
TAB_SIZE_PX = 50
FIRST_WRONG_FEEDBACK = {
    "incomplete": "Your answer is correct so far, but it is incomplete.",
    "wrong-at-block": r"""Your answer is incorrect starting at <span style="color:red;">block number {}</span>.
        The problem is most likely one of the following:
        <ul><li> This block is not a part of the correct solution </li>
        <li>This block needs to come after a block that did not appear before it </li>""",
    "indentation": r"""<li>This line is indented incorrectly </li>""",
    "block-group": r"""<li> You have attempted to start a new section of the answer without finishing the previous section </li>""",
}


def filter_multiple_from_array(
    data: list[dict[str, Any]], keys: list[str]
) -> list[dict[str, Any]]:
    return [{key: item[key] for key in keys} for item in data]


def get_graph_info(html_tags: lxml.html.HtmlElement) -> tuple[str, list[str]]:
    tag = pl.get_string_attrib(html_tags, "tag", pl.get_uuid()).strip()
    depends = pl.get_string_attrib(html_tags, "depends", "")
    depends = [tag.strip() for tag in depends.split(",")] if depends else []
    return tag, depends


def extract_dag(
    answers_list: list[OrderBlocksAnswerData],
) -> tuple[dict[str, list[str]], dict[str, Optional[str]]]:
    depends_graph = {ans["tag"]: ans["depends"] for ans in answers_list}
    group_belonging = {ans["tag"]: ans["group_info"]["tag"] for ans in answers_list}
    group_depends = {
        ans["group_info"]["tag"]: ans["group_info"]["depends"]
        for ans in answers_list
        if ans["group_info"]["depends"] is not None
        and ans["group_info"]["tag"] is not None
    }
    depends_graph.update(group_depends)
    return depends_graph, group_belonging


def solve_problem(
    answers_list: list[OrderBlocksAnswerData], grading_method: GradingMethodType
) -> list[OrderBlocksAnswerData]:
    if (
        grading_method is GradingMethodType.EXTERNAL
        or grading_method is GradingMethodType.UNORDERED
        or grading_method is GradingMethodType.ORDERED
    ):
        return answers_list
    elif grading_method is GradingMethodType.RANKING:
        return sorted(answers_list, key=lambda x: int(x["ranking"]))
    elif grading_method is GradingMethodType.DAG:
        depends_graph, group_belonging = extract_dag(answers_list)
        solution = solve_dag(depends_graph, group_belonging)
        return sorted(answers_list, key=lambda x: solution.index(x["tag"]))
    else:
        assert_never(grading_method)


def prepare(element_html: str, data: pl.QuestionData) -> None:
    element = lxml.html.fragment_fromstring(element_html)

    required_attribs = ["answers-name"]
    optional_attribs = [
        "source-blocks-order",
        "grading-method",
        "indentation",
        "source-header",
        "solution-header",
        "file-name",
        "solution-placement",
        "max-incorrect",
        "min-incorrect",
        "weight",
        "inline",
        "max-indent",
        "feedback",
        "partial-credit",
        "format",
        "code-language",
        "allow-blank",
    ]

    pl.check_attribs(
        element, required_attribs=required_attribs, optional_attribs=optional_attribs
    )
    answer_name = pl.get_string_attrib(element, "answers-name")
    pl.check_answers_names(data, answer_name)

    check_indentation = pl.get_boolean_attrib(element, "indentation", INDENTION_DEFAULT)
    grading_method = pl.get_enum_attrib(
        element, "grading-method", GradingMethodType, GRADING_METHOD_DEFAULT
    )
    feedback_type = pl.get_enum_attrib(
        element, "feedback", FeedbackType, FEEDBACK_DEFAULT
    )

    if (
        grading_method is not GradingMethodType.DAG
        and grading_method is not GradingMethodType.RANKING
        and pl.has_attrib(element, "partial-credit")
    ):
        raise Exception(
            "You may only specify partial credit options in the DAG and ranking grading modes."
        )

    if (
        grading_method is not GradingMethodType.DAG
        and grading_method is not GradingMethodType.RANKING
        and feedback_type is not FeedbackType.NONE
    ):
        raise Exception(
            "feedback type {feedback_type.value} is not available with the {grading_method.value} grading-method."
        )

    format = pl.get_enum_attrib(element, "format", FormatType, FormatType.DEFAULT)
    code_language = pl.get_string_attrib(element, "code-language", None)
    if format is FormatType.DEFAULT and code_language is not None:
        raise Exception('code-language attribute may only be used with format="code"')

    correct_answers: list[OrderBlocksAnswerData] = []
    incorrect_answers: list[OrderBlocksAnswerData] = []
    used_tags = set()

    def prepare_tag(
        html_tags: lxml.html.HtmlElement,
        index: int,
        group_info: GroupInfo,
    ):
        if html_tags.tag != "pl-answer":
            raise Exception(
                "Any html tags nested inside <pl-order-blocks> must be <pl-answer> or <pl-block-group>. \
                Any html tags nested inside <pl-block-group> must be <pl-answer>"
            )

        if grading_method is GradingMethodType.EXTERNAL:
            pl.check_attribs(
                html_tags, required_attribs=[], optional_attribs=["correct"]
            )
        elif grading_method in [GradingMethodType.UNORDERED, GradingMethodType.ORDERED]:
            pl.check_attribs(
                html_tags, required_attribs=[], optional_attribs=["correct", "indent"]
            )
        elif grading_method is GradingMethodType.RANKING:
            pl.check_attribs(
                html_tags,
                required_attribs=[],
                optional_attribs=[
                    "correct",
                    "tag",
                    "ranking",
                    "indent",
                    "distractor-for",
                ],
            )
        elif grading_method is GradingMethodType.DAG:
            pl.check_attribs(
                html_tags,
                required_attribs=[],
                optional_attribs=[
                    "correct",
                    "tag",
                    "depends",
                    "comment",
                    "indent",
                    "distractor-for",
                ],
            )

        is_correct = pl.get_boolean_attrib(
            html_tags, "correct", PL_ANSWER_CORRECT_DEFAULT
        )
        answer_indent = pl.get_integer_attrib(html_tags, "indent", None)
        inner_html = pl.inner_html(html_tags)
        ranking = pl.get_integer_attrib(html_tags, "ranking", -1)
        distractor_for = pl.get_string_attrib(html_tags, "distractor-for", None)
        if distractor_for is not None and is_correct:
            raise Exception(
                "The distractor-for attribute may only be used on blocks with correct=false."
            )

        tag, depends = get_graph_info(html_tags)
        if is_correct:
            if tag in used_tags:
                raise Exception(
                    f'Tag "{tag}" used in multiple places. The tag attribute for each <pl-answer> and <pl-block-group> must be unique.'
                )
            else:
                used_tags.add(tag)

        if check_indentation is False and answer_indent is not None:
            raise Exception(
                "<pl-answer> should not specify indentation if indentation is disabled."
            )

        if format is FormatType.CODE:
            inner_html = (
                "<pl-code"
                + (' language="' + code_language + '"' if code_language else "")
                + ">"
                + inner_html
                + "</pl-code>"
            )

        answer_data_dict: OrderBlocksAnswerData = {
            "inner_html": inner_html,
            "indent": answer_indent,
            "ranking": ranking,
            "index": index,
            "tag": tag,
            "distractor_for": distractor_for,
            "depends": depends,  # only used with DAG grader
            "group_info": group_info,  # only used with DAG grader
            "uuid": pl.get_uuid(),
        }
        if is_correct:
            correct_answers.append(answer_data_dict)
        else:
            incorrect_answers.append(answer_data_dict)

    index = 0
    for html_tags in element:  # iterate through the html tags inside pl-order-blocks
        if html_tags.tag is etree.Comment:
            continue
        elif html_tags.tag == "pl-block-group":
            if grading_method is not GradingMethodType.DAG:
                raise Exception(
                    'Block groups only supported in the "dag" grading mode.'
                )

            group_tag, group_depends = get_graph_info(html_tags)
            if group_tag in used_tags:
                raise Exception(
                    f'Tag "{group_tag}" used in multiple places. The tag attribute for each <pl-answer> and <pl-block-group> must be unique.'
                )
            else:
                used_tags.add(group_tag)

            for grouped_tag in html_tags:
                if html_tags.tag is etree.Comment:
                    continue
                else:
                    prepare_tag(
                        grouped_tag, index, {"tag": group_tag, "depends": group_depends}
                    )
                    index += 1
        else:
            prepare_tag(html_tags, index, {"tag": None, "depends": None})
            index += 1

    if grading_method is not GradingMethodType.EXTERNAL and len(correct_answers) == 0:
        raise Exception("There are no correct answers specified for this question.")

    all_incorrect_answers = len(incorrect_answers)
    max_incorrect = pl.get_integer_attrib(
        element, "max-incorrect", all_incorrect_answers
    )
    min_incorrect = pl.get_integer_attrib(
        element, "min-incorrect", all_incorrect_answers
    )

    if min_incorrect > len(incorrect_answers) or max_incorrect > len(incorrect_answers):
        raise Exception(
            "The min-incorrect or max-incorrect attribute may not exceed the number of incorrect <pl-answers>."
        )
    if min_incorrect > max_incorrect:
        raise Exception(
            "The attribute min-incorrect must be smaller than max-incorrect."
        )

    incorrect_answers_count = random.randint(min_incorrect, max_incorrect)

    sampled_correct_answers = correct_answers
    sampled_incorrect_answers = random.sample(
        incorrect_answers, incorrect_answers_count
    )

    all_blocks = sampled_correct_answers + sampled_incorrect_answers

    source_blocks_order = pl.get_enum_attrib(
        element,
        "source-blocks-order",
        SourceBlocksOrderType,
        SOURCE_BLOCKS_ORDER_DEFAULT,
    )
    if source_blocks_order == SourceBlocksOrderType.RANDOM:
        random.shuffle(all_blocks)
    elif source_blocks_order == SourceBlocksOrderType.ORDERED:
        all_blocks.sort(key=lambda a: a["index"])
    elif source_blocks_order == SourceBlocksOrderType.ALPHABETIZED:
        all_blocks.sort(key=lambda a: a["inner_html"])
    else:
        assert_never(source_blocks_order)

    # prep for visual pairing
    correct_tags = set(block["tag"] for block in all_blocks if block["tag"] is not None)
    incorrect_tags = set(
        block["distractor_for"] for block in all_blocks if block["distractor_for"]
    )

    if not incorrect_tags.issubset(correct_tags):
        raise ValueError(
            f"The following distractor-for tags do not have matching correct answer tags: {incorrect_tags - correct_tags}"
        )

    for block in all_blocks:
        if block["distractor_for"] is not None:
            continue

        distractors = [
            block2
            for block2 in all_blocks
            if (block["tag"] == block2.get("distractor_for"))
            and (block["tag"] is not None)
        ]

        if len(distractors) == 0:
            continue

        distractor_bin = pl.get_uuid()
        block["distractor_bin"] = distractor_bin
        for distractor in distractors:
            distractor["distractor_bin"] = distractor_bin

    data["params"][answer_name] = all_blocks
    data["correct_answers"][answer_name] = correct_answers

    # if the order of the blocks in the HTML is a correct solution, leave it unchanged, but if it
    # isn't we need to change it into a solution before displaying it as such
    data_copy = deepcopy(data)
    data_copy["submitted_answers"] = {answer_name: correct_answers}
    data_copy["partial_scores"] = {}
    grade(element_html, data_copy)
    if data_copy["partial_scores"][answer_name]["score"] != 1:
        data["correct_answers"][answer_name] = solve_problem(
            correct_answers, grading_method
        )


def render(element_html: str, data: pl.QuestionData) -> str:
    element = lxml.html.fragment_fromstring(element_html)
    answer_name = pl.get_string_attrib(element, "answers-name")
    format = pl.get_enum_attrib(element, "format", FormatType, FormatType.DEFAULT)

    block_formatting = (
        "pl-order-blocks-code" if format is FormatType.CODE else "list-group-item"
    )
    grading_method = pl.get_enum_attrib(
        element, "grading-method", GradingMethodType, GRADING_METHOD_DEFAULT
    )

    if data["panel"] == "question":
        editable = data["editable"]

        answer_name = pl.get_string_attrib(element, "answers-name")
        source_header = pl.get_string_attrib(
            element, "source-header", SOURCE_HEADER_DEFAULT
        )
        solution_header = pl.get_string_attrib(
            element, "solution-header", SOLUTION_HEADER_DEFAULT
        )

        all_blocks = data["params"][answer_name]
        student_previous_submission = data["submitted_answers"].get(answer_name, [])
        submitted_block_ids = {block["uuid"] for block in student_previous_submission}
        source_blocks = [
            block for block in all_blocks if block["uuid"] not in submitted_block_ids
        ]

        for option in student_previous_submission:
            submission_indent = option.get("indent", None)
            if submission_indent is not None:
                submission_indent = int(submission_indent) * TAB_SIZE_PX
            option["indent"] = submission_indent

        dropzone_layout = pl.get_string_attrib(
            element, "solution-placement", SOLUTION_PLACEMENT_DEFAULT
        )
        check_indentation = pl.get_boolean_attrib(
            element, "indentation", INDENTION_DEFAULT
        )
        max_indent = pl.get_integer_attrib(element, "max-indent", MAX_INDENTION_DEFAULT)

        help_text = (
            "Drag answer tiles into the answer area to the " + dropzone_layout + ". "
        )

        if grading_method is GradingMethodType.UNORDERED:
            help_text += "<br>Your answer ordering does not matter. "
        elif grading_method is not GradingMethodType.EXTERNAL:
            help_text += "<br>The ordering of your answer matters and is graded."
        else:
            help_text += "<br>Your answer will be autograded; be sure to indent and order your answer properly."

        if check_indentation:
            help_text += "<br><b>Your answer should be indented. </b> Indent your tiles by dragging them horizontally in the answer area."

        uuid = pl.get_uuid()
        html_params = {
            "question": True,
            "answer_name": answer_name,
            "source-header": source_header,
            "solution-header": solution_header,
            "options": source_blocks,
            "submission_dict": student_previous_submission,
            "dropzone_layout": "pl-order-blocks-bottom"
            if dropzone_layout == "bottom"
            else "pl-order-blocks-right",
            "check_indentation": "true" if check_indentation else "false",
            "help_text": help_text,
            "max_indent": max_indent,
            "uuid": uuid,
            "block_formatting": block_formatting,
            "editable": editable,
        }

        with open("pl-order-blocks.mustache", "r", encoding="utf-8") as f:
            html = chevron.render(f, html_params)
        return html

    elif data["panel"] == "submission":
        if grading_method is GradingMethodType.EXTERNAL:
            return ""  # external grader is responsible for displaying results screen

        student_submission = ""
        score = None
        feedback = None
        if answer_name in data["submitted_answers"]:
            student_submission = [
                {
                    "inner_html": attempt["inner_html"],
                    "indent": (attempt["indent"] or 0) * TAB_SIZE_PX,
                }
                for attempt in data["submitted_answers"][answer_name]
            ]

        if answer_name in data["partial_scores"]:
            score = data["partial_scores"][answer_name]["score"]
            feedback = data["partial_scores"][answer_name].get("feedback", "")

        html_params = {
            "submission": True,
            "parse-error": data["format_errors"].get(answer_name, None),
            "student_submission": student_submission,
            "feedback": feedback,
            "block_formatting": block_formatting,
        }

        if score is not None:
            try:
                score = float(score * 100)
                if score >= 100:
                    html_params["correct"] = True
                elif score > 0:
                    html_params["partially_correct"] = math.floor(score)
                else:
                    html_params["incorrect"] = True
            except Exception:
                raise ValueError(
                    f"invalid score: {data['partial_scores'][answer_name].get('score', 0)}"
                )

        with open("pl-order-blocks.mustache", "r", encoding="utf-8") as f:
            html = chevron.render(f, html_params)
        return html

    elif data["panel"] == "answer":
        if grading_method is GradingMethodType.EXTERNAL:
            try:
                base_path = data["options"]["question_path"]
                file_lead_path = os.path.join(base_path, "tests/ans.py")
                with open(file_lead_path, "r") as file:
                    solution_file = file.read()
                return f'<pl-code language="python">{solution_file}</pl-code>'
            except FileNotFoundError:
                return "The reference solution is not provided for this question."

        if grading_method is GradingMethodType.UNORDERED:
            ordering_message = "in any order"
        elif grading_method in [GradingMethodType.DAG, GradingMethodType.RANKING]:
            ordering_message = "there may be other correct orders"
        else:
            ordering_message = "in the specified order"
        check_indentation = pl.get_boolean_attrib(
            element, "indentation", INDENTION_DEFAULT
        )

        required_indents = set(
            block["indent"] for block in data["correct_answers"][answer_name]
        )
        indentation_message = ""
        if check_indentation:
            if -1 not in required_indents:
                indentation_message = ", correct indentation required"
            elif len(required_indents) > 1:
                indentation_message = ", some blocks require correct indentation"

        question_solution = [
            {
                "inner_html": solution["inner_html"],
                "indent": max(0, (solution["indent"] or 0) * TAB_SIZE_PX),
            }
            for solution in data["correct_answers"][answer_name]
        ]

        html_params = {
            "true_answer": True,
            "question_solution": question_solution,
            "ordering_message": ordering_message,
            "indentation_message": indentation_message,
            "block_formatting": block_formatting,
        }
        with open("pl-order-blocks.mustache", "r", encoding="utf-8") as f:
            html = chevron.render(f, html_params)
        return html

    else:
        assert_never(data["panel"])


def parse(element_html: str, data: pl.QuestionData) -> None:
    element = lxml.html.fragment_fromstring(element_html)
    answer_name = pl.get_string_attrib(element, "answers-name")
    allow_blank_submission = pl.get_boolean_attrib(
        element, "allow-blank", ALLOW_BLANK_DEFAULT
    )

    answer_raw_name = answer_name + "-input"
    student_answer = data["raw_submitted_answers"].get(answer_raw_name, "[]")

    student_answer = json.loads(student_answer)

    if (not allow_blank_submission) and (
        student_answer is None or student_answer == []
    ):
        data["format_errors"][
            answer_name
        ] = "Your submitted answer was blank; you did not drag any answer blocks into the answer area."
        return

    grading_method = pl.get_enum_attrib(
        element, "grading-method", GradingMethodType, GRADING_METHOD_DEFAULT
    )
    correct_answers = data["correct_answers"][answer_name]

    if grading_method is GradingMethodType.RANKING:
        for answer in student_answer:
            search = next(
                (
                    item
                    for item in correct_answers
                    if item["inner_html"] == answer["inner_html"]
                ),
                None,
            )
            answer["ranking"] = search["ranking"] if search is not None else None
            answer["tag"] = search["tag"] if search is not None else None
    elif grading_method is GradingMethodType.DAG:
        for answer in student_answer:
            search = next(
                (
                    item
                    for item in correct_answers
                    if item["inner_html"] == answer["inner_html"]
                ),
                None,
            )
            answer["tag"] = search["tag"] if search is not None else None

    if grading_method is GradingMethodType.EXTERNAL:
        for html_tags in element:
            if html_tags.tag == "pl-answer":
                pl.check_attribs(html_tags, required_attribs=[], optional_attribs=[])
        file_name = pl.get_string_attrib(element, "file-name", FILE_NAME_DEFAULT)

        answer_code = ""
        for index, answer in enumerate(student_answer):
            indent = int(answer["indent"] or 0)
            answer_code += (
                ("    " * indent)
                + lxml.html.fromstring(answer["inner_html"]).text_content()  # type: ignore
                + "\n"
            )

        if len(answer_code) == 0:
            data["format_errors"]["_files"] = "The submitted file was empty."
        else:
            data["submitted_answers"]["_files"] = [
                {
                    "name": file_name,
                    "contents": base64.b64encode(answer_code.encode("utf-8")).decode(
                        "utf-8"
                    ),
                }
            ]

    data["submitted_answers"][answer_name] = student_answer
    if answer_raw_name in data["submitted_answers"]:
        del data["submitted_answers"][answer_raw_name]


def grade(element_html: str, data: pl.QuestionData) -> None:
    element = lxml.html.fragment_fromstring(element_html)
    answer_name = pl.get_string_attrib(element, "answers-name")

    student_answer = data["submitted_answers"][answer_name]
    grading_method = pl.get_enum_attrib(
        element, "grading-method", GradingMethodType, GRADING_METHOD_DEFAULT
    )
    check_indentation = pl.get_boolean_attrib(element, "indentation", INDENTION_DEFAULT)
    feedback_type = pl.get_enum_attrib(
        element, "feedback", FeedbackType, FEEDBACK_DEFAULT
    )
    answer_weight = pl.get_integer_attrib(element, "weight", WEIGHT_DEFAULT)
    partial_credit_type = pl.get_enum_attrib(
        element, "partial-credit", PartialCreditType, PartialCreditType.LCS
    )

    true_answer_list = data["correct_answers"][answer_name]

    final_score = 0
    feedback = ""
    first_wrong = None

    if check_indentation:
        indentations = {ans["uuid"]: ans["indent"] for ans in true_answer_list}
        for ans in student_answer:
            indentation = indentations.get(ans["uuid"])
            if indentation != -1 and ans["indent"] != indentation:
                if "tag" in ans:
                    ans["tag"] = None
                else:
                    ans["inner_html"] = None

    if grading_method is GradingMethodType.UNORDERED:
        true_answer_uuids = set(ans["uuid"] for ans in true_answer_list)
        student_answer_uuids = set(ans["uuid"] for ans in student_answer)
        correct_selections = len(true_answer_uuids.intersection(student_answer_uuids))
        incorrect_selections = len(student_answer) - correct_selections

        final_score = float((correct_selections - incorrect_selections)) / len(
            true_answer_list
        )
        final_score = max(0.0, final_score)  # scores cannot be below 0

    elif grading_method is GradingMethodType.ORDERED:
        student_answer = [ans["inner_html"] for ans in student_answer]
        true_answer = [ans["inner_html"] for ans in true_answer_list]
        final_score = 1 if student_answer == true_answer else 0

    elif grading_method in [GradingMethodType.RANKING, GradingMethodType.DAG]:
        submission = [ans["tag"] for ans in student_answer]
        depends_graph = {}
        group_belonging = {}

        if grading_method is GradingMethodType.RANKING:
            true_answer_list = sorted(true_answer_list, key=lambda x: int(x["ranking"]))
            true_answer = [answer["tag"] for answer in true_answer_list]
            tag_to_rank = {
                answer["tag"]: answer["ranking"] for answer in true_answer_list
            }
            lines_of_rank = {
                rank: [tag for tag in tag_to_rank if tag_to_rank[tag] == rank]
                for rank in set(tag_to_rank.values())
            }

            cur_rank_depends = []
            prev_rank = None
            for tag in true_answer:
                ranking = tag_to_rank[tag]
                if prev_rank is not None and ranking != prev_rank:
                    cur_rank_depends = lines_of_rank[prev_rank]
                depends_graph[tag] = cur_rank_depends
                prev_rank = ranking

        elif grading_method is GradingMethodType.DAG:
            depends_graph, group_belonging = extract_dag(true_answer_list)

        num_initial_correct, true_answer_length = grade_dag(
            submission, depends_graph, group_belonging
        )
        first_wrong = (
            None if num_initial_correct == len(submission) else num_initial_correct
        )

        if partial_credit_type is PartialCreditType.NONE:
            if num_initial_correct == true_answer_length:
                final_score = 1
            elif num_initial_correct < true_answer_length:
                final_score = 0
        elif partial_credit_type is PartialCreditType.LCS:
            edit_distance = lcs_partial_credit(
                submission, depends_graph, group_belonging
            )
            final_score = max(
                0, float(true_answer_length - edit_distance) / true_answer_length
            )

        if final_score < 1:
            if feedback_type is FeedbackType.NONE:
                feedback = ""
            elif feedback_type is FeedbackType.FIRST_WRONG:
                if first_wrong is None:
                    feedback = FIRST_WRONG_FEEDBACK["incomplete"]
                else:
                    feedback = FIRST_WRONG_FEEDBACK["wrong-at-block"].format(
                        str(first_wrong + 1)
                    )
                    has_block_groups = group_belonging != {} and set(
                        group_belonging.values()
                    ) != {None}
                    if check_indentation:
                        feedback += FIRST_WRONG_FEEDBACK["indentation"]
                    if has_block_groups:
                        feedback += FIRST_WRONG_FEEDBACK["block-group"]
                    feedback += "</ul>"

    data["partial_scores"][answer_name] = {
        "score": round(final_score, 2),
        "feedback": feedback,
        "weight": answer_weight,
    }


def test(element_html: str, data: pl.ElementTestData) -> None:
    element = lxml.html.fragment_fromstring(element_html)
    grading_method = pl.get_enum_attrib(
        element, "grading-method", GradingMethodType, GRADING_METHOD_DEFAULT
    )
    answer_name = pl.get_string_attrib(element, "answers-name")
    answer_name_field = answer_name + "-input"
    weight = pl.get_integer_attrib(element, "weight", WEIGHT_DEFAULT)
    feedback_type = pl.get_enum_attrib(
        element, "feedback", FeedbackType, FEEDBACK_DEFAULT
    )
    partial_credit_type = pl.get_enum_attrib(
        element, "partial-credit", PartialCreditType, PartialCreditType.LCS
    )

    # Right now invalid input must mean an empty response. Because user input is only
    # through drag and drop, there is no other way for their to be invalid input. This
    # may change in the future if we have nested input boxes (like faded parsons' problems).
    if data["test_type"] == "invalid":
        data["raw_submitted_answers"][answer_name_field] = json.dumps([])
        data["format_errors"][answer_name] = "No answer was submitted."

    # TODO grading modes 'unordered,' 'dag,' and 'ranking' allow multiple different possible
    # correct answers, we should check them at random instead of just the provided solution
    elif data["test_type"] == "correct":
        answer = filter_multiple_from_array(
            data["correct_answers"][answer_name], ["inner_html", "indent", "uuid"]
        )
        data["raw_submitted_answers"][answer_name_field] = json.dumps(answer)
        data["partial_scores"][answer_name] = {
            "score": 1,
            "weight": weight,
            "feedback": "",
        }

    # TODO: The only wrong answer being tested is the correct answer with the first
    # block mising. We should instead do a random selection of correct and incorrect blocks.
    elif data["test_type"] == "incorrect":
        answer = filter_multiple_from_array(
            data["correct_answers"][answer_name], ["inner_html", "indent", "uuid"]
        )
        answer.pop(0)
        score = 0
        if grading_method is GradingMethodType.UNORDERED or (
            (
                grading_method is GradingMethodType.DAG
                or grading_method is GradingMethodType.RANKING
            )
            and partial_credit_type is PartialCreditType.LCS
        ):
            score = round(float(len(answer)) / (len(answer) + 1), 2)

        if (
            grading_method is GradingMethodType.DAG
            and feedback_type is FeedbackType.FIRST_WRONG
        ):
            feedback = FIRST_WRONG_FEEDBACK["wrong-at-block"].format(1)
            group_belonging = {
                ans["tag"]: ans["group_info"]["tag"]
                for ans in data["correct_answers"][answer_name]
            }
            has_block_groups = group_belonging != {} and set(
                group_belonging.values()
            ) != {None}
            if has_block_groups:
                feedback += FIRST_WRONG_FEEDBACK["block-group"]
            feedback += "</ul>"
        else:
            feedback = ""

        data["raw_submitted_answers"][answer_name_field] = json.dumps(answer)
        data["partial_scores"][answer_name] = {
            "score": score,
            "weight": weight,
            "feedback": feedback,
        }

    else:
        raise Exception("invalid result: %s" % data["test_type"])
