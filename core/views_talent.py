"""Recruitment (job openings, candidates) aur performance reviews."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .filters import parse_int
from .forms_office import CandidateForm, JobForm, ManagerReviewForm, ReviewCycleForm, SelfReviewForm
from .models import Candidate, JobOpening, Review, ReviewCycle
from .permissions import hr_required, is_hr
from .services import display_name, notify

# ---------------------------------------------------------------- recruitment


@login_required
@hr_required
def job_list(request):
    status = request.GET.get('status')
    jobs = JobOpening.objects.select_related('department').annotate(
        total=Count('candidates'),
        active=Count('candidates', filter=~Q(candidates__stage__in=('hired', 'rejected'))),
        hired=Count('candidates', filter=Q(candidates__stage='hired')),
    )
    if status in dict(JobOpening.STATUS_CHOICES):
        jobs = jobs.filter(status=status)
    else:
        status = ''
    return render(request, 'core/jobs.html', {
        'jobs': jobs, 'current_status': status, 'status_choices': JobOpening.STATUS_CHOICES,
    })


@login_required
@hr_required
def job_add(request):
    form = JobForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        job = form.save(commit=False)
        job.created_by = request.user
        job.save()
        messages.success(request, 'Job opening created.')
        return redirect('job_detail', pk=job.pk)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'New Job Opening', 'back_url': 'job_list'})


@login_required
@hr_required
def job_edit(request, pk):
    job = get_object_or_404(JobOpening, pk=pk)
    form = JobForm(request.POST or None, instance=job)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Job opening updated.')
        return redirect('job_detail', pk=pk)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': f'Edit {job.title}', 'back_url': 'job_list'})


@login_required
@hr_required
def job_delete(request, pk):
    job = get_object_or_404(JobOpening, pk=pk)
    if request.method == 'POST':
        job.delete()
        messages.success(request, 'Job opening and its candidates were deleted.')
        return redirect('job_list')
    return render(request, 'core/confirm_delete.html', {
        'page_title': 'Delete Job Opening', 'heading': f'Delete "{job.title}"?',
        'message': f'This also deletes its {job.candidates.count()} candidate(s). It cannot be undone.',
        'cancel_url': reverse('job_detail', args=[pk]),
    })


@login_required
@hr_required
def job_detail(request, pk):
    job = get_object_or_404(JobOpening.objects.select_related('department'), pk=pk)
    candidates = list(job.candidates.all())
    columns = []
    for value, label in Candidate.STAGE_CHOICES:
        columns.append({'value': value, 'label': label, 'items': [c for c in candidates if c.stage == value]})
    hired = sum(1 for c in candidates if c.stage == 'hired')
    return render(request, 'core/job_detail.html', {
        'job': job, 'columns': columns, 'hired': hired, 'total': len(candidates),
        'stage_choices': Candidate.STAGE_CHOICES,
    })


@login_required
@hr_required
def candidate_add(request, job_id):
    job = get_object_or_404(JobOpening, pk=job_id)
    form = CandidateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        candidate = form.save(commit=False)
        candidate.job = job
        candidate.save()
        messages.success(request, f'Added {candidate.name}.')
        return redirect('job_detail', pk=job.pk)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Add Candidate - {job.title}', 'back_url': 'job_list',
        'help': 'Resume: PDF or Word, up to 10 MB.',
    })


@login_required
@hr_required
def candidate_edit(request, pk):
    candidate = get_object_or_404(Candidate.objects.select_related('job'), pk=pk)
    form = CandidateForm(request.POST or None, request.FILES or None, instance=candidate)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Candidate updated.')
        return redirect('job_detail', pk=candidate.job_id)
    return render(request, 'core/simple_form.html', {
        'form': form, 'page_title': f'Edit {candidate.name}', 'back_url': 'job_list',
    })


@login_required
@hr_required
@require_POST
def candidate_stage(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    stage = request.POST.get('stage')
    if stage not in dict(Candidate.STAGE_CHOICES):
        messages.error(request, 'Pick a valid stage.')
    else:
        candidate.stage = stage
        candidate.save(update_fields=['stage', 'updated_at'])
        messages.success(request, f'{candidate.name} moved to {candidate.get_stage_display()}.')
    return redirect('job_detail', pk=candidate.job_id)


@login_required
@hr_required
@require_POST
def candidate_delete(request, pk):
    candidate = get_object_or_404(Candidate, pk=pk)
    job_id = candidate.job_id
    candidate.delete()
    messages.success(request, 'Candidate deleted.')
    return redirect('job_detail', pk=job_id)


# ---------------------------------------------------------------- performance reviews

@login_required
def review_home(request):
    """Har kisi ke liye: meri reviews aur jo mujhe deni hain. HR ko cycles bhi dikhte hain."""
    mine = Review.objects.filter(employee=request.user).select_related('cycle')
    to_give = Review.objects.filter(reviewer=request.user).exclude(employee=request.user).select_related('cycle', 'employee')
    hr = is_hr(request.user)
    cycles = ReviewCycle.objects.annotate(
        total=Count('reviews'), done=Count('reviews', filter=Q(reviews__manager_submitted_at__isnull=False)),
    ) if hr else []
    return render(request, 'core/reviews.html', {'mine': mine, 'to_give': to_give, 'cycles': cycles, 'hr': hr})


@login_required
@hr_required
def cycle_add(request):
    form = ReviewCycleForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cycle = form.save()
        messages.success(request, 'Review cycle created. Now add everyone to it.')
        return redirect('cycle_detail', pk=cycle.pk)
    return render(request, 'core/simple_form.html', {'form': form, 'page_title': 'New Review Cycle', 'back_url': 'review_home'})


@login_required
@hr_required
def cycle_detail(request, pk):
    cycle = get_object_or_404(ReviewCycle, pk=pk)
    reviews = cycle.reviews.select_related('employee', 'reviewer')
    ratings = [r.manager_rating for r in reviews if r.manager_rating]
    return render(request, 'core/cycle_detail.html', {
        'cycle': cycle, 'reviews': reviews,
        'average': round(sum(ratings) / len(ratings), 2) if ratings else None,
        'self_done': sum(1 for r in reviews if r.self_submitted_at),
        'mgr_done': len(ratings),
    })


@login_required
@hr_required
@require_POST
def cycle_generate(request, pk):
    cycle = get_object_or_404(ReviewCycle, pk=pk)
    if not cycle.is_open:
        messages.error(request, 'This cycle is closed.')
        return redirect('cycle_detail', pk=pk)
    existing = set(cycle.reviews.values_list('employee_id', flat=True))
    created = 0
    for user in User.objects.filter(is_active=True).select_related('profile'):
        if user.pk in existing:
            continue
        profile = getattr(user, 'profile', None)
        manager = profile.reporting_manager if profile else None
        if manager is not None and (manager.pk == user.pk or not manager.is_active):
            manager = None
        try:
            with transaction.atomic():
                Review.objects.create(cycle=cycle, employee=user, reviewer=manager)
        except IntegrityError:
            continue
        created += 1
        notify(user, f'Review cycle "{cycle.name}" is open. Please add your self review.',
               link=reverse('review_home'), actor=request.user, request=request)
    messages.success(request, f'Added {created} employee(s) to the cycle.' if created else 'Everyone is already in this cycle.')
    return redirect('cycle_detail', pk=pk)


@login_required
@hr_required
@require_POST
def cycle_toggle(request, pk):
    cycle = get_object_or_404(ReviewCycle, pk=pk)
    cycle.is_open = not cycle.is_open
    cycle.save(update_fields=['is_open'])
    messages.success(request, 'Cycle reopened.' if cycle.is_open else 'Cycle closed.')
    return redirect('cycle_detail', pk=pk)


@login_required
@hr_required
@require_POST
def cycle_delete(request, pk):
    get_object_or_404(ReviewCycle, pk=pk).delete()
    messages.success(request, 'Cycle deleted.')
    return redirect('review_home')


def _review_or_404(user, pk):
    review = get_object_or_404(Review.objects.select_related('cycle', 'employee', 'reviewer'), pk=pk)
    if not (is_hr(user) or review.employee_id == user.id or review.reviewer_id == user.id):
        raise Http404
    return review


def _can_self(user, review):
    return review.employee_id == user.id and review.cycle.is_open and review.self_submitted_at is None


def _can_manage(user, review):
    if not review.cycle.is_open or review.manager_submitted_at is not None or review.employee_id == user.id:
        return False
    return review.reviewer_id == user.id or (review.reviewer_id is None and is_hr(user))


@login_required
def review_detail(request, pk):
    review = _review_or_404(request.user, pk)
    is_employee = review.employee_id == request.user.id
    hr = is_hr(request.user)
    show_manager = review.manager_submitted_at is not None or not is_employee
    return render(request, 'core/review_detail.html', {
        'review': review, 'hr': hr, 'is_employee': is_employee, 'show_manager': show_manager,
        'self_form': SelfReviewForm(instance=review) if _can_self(request.user, review) else None,
        'manager_form': ManagerReviewForm(instance=review) if _can_manage(request.user, review) else None,
        'people': User.objects.filter(is_active=True).order_by('username') if hr else None,
    })


@login_required
@require_POST
def review_self(request, pk):
    review = _review_or_404(request.user, pk)
    if not _can_self(request.user, review):
        messages.error(request, 'You cannot submit this self review.')
        return redirect('review_detail', pk=pk)
    form = SelfReviewForm(request.POST, instance=review)
    if form.is_valid():
        item = form.save(commit=False)
        item.self_submitted_at = timezone.now()
        item.save()
        notify(review.reviewer, f'{display_name(request.user)} submitted a self review',
               link=reverse('review_detail', args=[pk]), actor=request.user, request=request)
        messages.success(request, 'Self review submitted.')
    else:
        messages.error(request, ' '.join(e for errs in form.errors.values() for e in errs))
    return redirect('review_detail', pk=pk)


@login_required
@require_POST
def review_manager(request, pk):
    review = _review_or_404(request.user, pk)
    if not _can_manage(request.user, review):
        messages.error(request, 'You cannot submit this review.')
        return redirect('review_detail', pk=pk)
    form = ManagerReviewForm(request.POST, instance=review)
    if form.is_valid():
        item = form.save(commit=False)
        item.manager_submitted_at = timezone.now()
        if item.reviewer_id is None:
            item.reviewer = request.user
        item.save()
        notify(review.employee, f'Your review for "{review.cycle.name}" is ready',
               link=reverse('review_detail', args=[pk]), actor=request.user, request=request)
        messages.success(request, 'Review submitted.')
    else:
        messages.error(request, ' '.join(e for errs in form.errors.values() for e in errs))
    return redirect('review_detail', pk=pk)


@login_required
@hr_required
@require_POST
def review_reviewer(request, pk):
    review = get_object_or_404(Review, pk=pk)
    if review.manager_submitted_at is not None:
        messages.error(request, 'The review is already submitted.')
        return redirect('review_detail', pk=pk)
    user_id = parse_int(request.POST.get('reviewer'))
    reviewer = User.objects.filter(pk=user_id, is_active=True).first() if user_id else None
    if reviewer is not None and reviewer.pk == review.employee_id:
        messages.error(request, 'Nobody can review themselves.')
    else:
        review.reviewer = reviewer
        review.save(update_fields=['reviewer'])
        messages.success(request, 'Reviewer updated.')
    return redirect('review_detail', pk=pk)
