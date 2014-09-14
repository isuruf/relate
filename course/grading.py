# -*- coding: utf-8 -*-

from __future__ import division

__copyright__ = "Copyright (C) 2014 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


from django.shortcuts import (  # noqa
        get_object_or_404, redirect)
from django.db import transaction
from django.core.exceptions import (  # noqa
        PermissionDenied, SuspiciousOperation,
        ObjectDoesNotExist)
from django import http
from django.utils.timezone import now

from course.models import (
        FlowSession, FlowPageVisitGrade,
        get_flow_grading_opportunity)
from course.constants import participation_role
from course.utils import (
        course_view, render_course_page,
        FlowPageContext)


@course_view
@transaction.atomic
def grade_flow_page(pctx, flow_session_id, page_ordinal):
    if pctx.role not in [
            participation_role.instructor,
            participation_role.teaching_assistant]:
        raise PermissionDenied("must be instructor or TA to view grades")

    flow_session = get_object_or_404(FlowSession, id=int(flow_session_id))

    if flow_session.course.pk != pctx.course.pk:
        raise SuspiciousOperation("Flow session not part of specified course")

    fpctx = FlowPageContext(pctx.repo, pctx.course, flow_session.flow_id,
            page_ordinal, participation=flow_session.participation,
            flow_session=flow_session)

    if fpctx.page_desc is None:
        raise http.Http404()

    # {{{ enable flow session zapping

    all_flow_sessions = list(FlowSession.objects
            .filter(
                course=pctx.course,
                flow_id=flow_session.flow_id,
                in_progress=flow_session.in_progress,
                for_credit=flow_session.for_credit)
            .order_by(
                "participation__user__last_name",
                "start_time"))

    next_flow_session_id = None
    prev_flow_session_id = None
    for i, other_flow_session in enumerate(all_flow_sessions):
        if other_flow_session.pk == flow_session.pk:
            if i > 0:
                prev_flow_session_id = all_flow_sessions[i-1].id
            if i + 1 < len(all_flow_sessions):
                next_flow_session_id = all_flow_sessions[i+1].id

    # }}}

    # {{{ reproduce student view

    if fpctx.page.expects_answer():
        if fpctx.prev_answer_visit is not None:
            answer_data = fpctx.prev_answer_visit.answer

            most_recent_grade = fpctx.prev_answer_visit.get_most_recent_grade()
            if most_recent_grade is not None:
                if most_recent_grade.feedback is not None:
                    from course.page import AnswerFeedback
                    feedback = AnswerFeedback.from_json(
                            most_recent_grade.feedback)
                else:
                    feedback = None

                grade_data = most_recent_grade.grade_data
            else:
                feedback = None
                grade_data = None

        else:
            feedback = None

        form = fpctx.page.make_form(
                fpctx.page_context, fpctx.page_data.data,
                answer_data, answer_is_final=True)
    else:
        form = None
        feedback = None

    if form is not None:
        form_html = fpctx.page.form_to_html(
                pctx.request, fpctx.page_context, form, answer_data)
    else:
        form_html = None

    max_points = None
    points_awarded = None
    if fpctx.page.expects_answer():
        max_points = fpctx.page.max_points(fpctx.page_data)
        if feedback is not None and feedback.correctness is not None:
            points_awarded = max_points * feedback.correctness

    # }}}

    # {{{ grading form

    if fpctx.page.expects_answer() and fpctx.prev_answer_visit is not None:
        request = pctx.request
        if pctx.request.method == "POST":
            grading_form = fpctx.page.post_grading_form(
                    fpctx.page_context, fpctx.page_data, grade_data,
                    request.POST, request.FILES)
            if grading_form.is_valid():
                grade_data = fpctx.page.update_grade_data_from_grading_form(
                        fpctx.page_context, fpctx.page_data, grade_data,
                        grading_form, request.FILES)

                feedback = fpctx.page.grade(
                        fpctx.page_context, fpctx.page_data,
                        answer_data, grade_data)

                if feedback is not None:
                    correctness = feedback.correctness
                else:
                    correctness = None

                if feedback is not None:
                    feedback_json = feedback.as_json()
                else:
                    feedback_json = None

                grade = FlowPageVisitGrade(
                        visit=fpctx.prev_answer_visit,
                        grader=pctx.request.user,
                        graded_at_git_commit_sha=fpctx.flow_commit_sha,

                        grade_data=grade_data,

                        max_points=fpctx.page.max_points(fpctx.page_data),
                        correctness=correctness,
                        feedback=feedback_json)

                grade.save()

                current_access_rule = fpctx.get_current_access_rule(
                        flow_session, flow_session.participation.role,
                        flow_session.participation, now())

                from course.flow import grade_flow_session
                grade_flow_session(fpctx, flow_session, current_access_rule)

        else:
            grading_form = fpctx.page.make_grading_form(
                    fpctx.page_context, fpctx.page_data, grade_data)

    else:
        grading_form = None

    if grading_form is not None:
        from crispy_forms.layout import Submit
        grading_form.helper.add_input(
                Submit(
                    "submit", "Submit",
                    accesskey="s",
                    css_class="col-lg-offset-2 cf-grading-save-button"))

        grading_form_html = fpctx.page.grading_form_to_html(
                pctx.request, fpctx.page_context, grading_form, grade_data)

    else:
        grading_form_html = None

    # }}}

    grading_opportunity = get_flow_grading_opportunity(
            pctx.course, flow_session.flow_id, fpctx.flow_desc)

    return render_course_page(
            pctx,
            "course/grade-flow-page.html",
            {
                "flow_identifier": fpctx.flow_identifier,
                "flow_session": flow_session,
                "flow_desc": fpctx.flow_desc,
                "ordinal": fpctx.ordinal,
                "page_data": fpctx.page_data,

                "body": fpctx.page.body(
                    fpctx.page_context, fpctx.page_data.data),
                "form": form,
                "form_html": form_html,
                "feedback": feedback,
                "max_points": max_points,
                "points_awarded": points_awarded,

                "grading_opportunity": grading_opportunity,

                "prev_flow_session_id": prev_flow_session_id,
                "next_flow_session_id": next_flow_session_id,

                "grading_form": grading_form,
                "grading_form_html": grading_form_html,
            })


# vim: foldmethod=marker