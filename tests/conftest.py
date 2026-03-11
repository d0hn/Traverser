"""Shared test fixtures."""

from __future__ import annotations

import pytest

from traverser.models.repo_models import FileNode, Language, RepoInfo


PYTHON_SAMPLE = '''\
"""Sample Python module for testing."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from .utils import helper_function
from ..config import Config

MY_CONSTANT = "hello"
DEBUG_FLAG = True


class BaseProcessor:
    """Base class for processors."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._cache: dict[str, str] = {}

    def process(self, data: str) -> str:
        """Process the given data string."""
        raise NotImplementedError

    @staticmethod
    def validate(data: str) -> bool:
        """Validate input data."""
        return bool(data.strip())


class DataProcessor(BaseProcessor):
    """Concrete processor implementation."""

    DEFAULT_TIMEOUT: int = 30

    def process(self, data: str) -> str:
        if not self.validate(data):
            raise ValueError("Empty data")
        return data.upper()

    async def process_async(self, data: str) -> str:
        """Async version of process."""
        return self.process(data)


def standalone_function(path: Path, optional_arg: Optional[str] = None) -> list[str]:
    """A standalone function at module level."""
    return [str(path)]


async def async_helper(value: int) -> bool:
    """An async helper."""
    return value > 0
'''

JS_SAMPLE = """\
import { Component, useState } from 'react';
import axios from 'axios';
import * as utils from './utils';
const config = require('./config');

export const API_BASE_URL = 'https://api.example.com';
const TIMEOUT = 5000;

export class ApiClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
  }

  async get(endpoint) {
    const response = await axios.get(`${this.baseUrl}${endpoint}`);
    return response.data;
  }
}

export function createClient(baseUrl = API_BASE_URL) {
  return new ApiClient(baseUrl);
}

const fetchData = async (url) => {
  const client = createClient();
  return client.get(url);
};

export default fetchData;
"""

TS_SAMPLE = """\
import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { retry, catchError } from 'rxjs/operators';
import type { User, ApiResponse } from './types';

export const DEFAULT_TIMEOUT = 10000;

export interface UserService {
  getUser(id: string): Observable<User>;
  updateUser(id: string, data: Partial<User>): Observable<ApiResponse>;
}

@Injectable({ providedIn: 'root' })
export class UserServiceImpl implements UserService {
  private apiUrl = '/api/users';

  constructor(private http: HttpClient) {}

  getUser(id: string): Observable<User> {
    return this.http.get<User>(`${this.apiUrl}/${id}`).pipe(retry(3));
  }

  updateUser(id: string, data: Partial<User>): Observable<ApiResponse> {
    return this.http.put<ApiResponse>(`${this.apiUrl}/${id}`, data);
  }
}

export const createHeaders = (token: string): HttpHeaders =>
  new HttpHeaders({ Authorization: `Bearer ${token}` });
"""

NESTJS_SAMPLE = """\
import { Controller, Get, Post, Delete, Body, Param, UseGuards, Injectable, Module } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { JwtAuthGuard } from './guards/jwt-auth.guard';
import { CreateUserDto } from './dto/create-user.dto';
import { User } from './entities/user.entity';

export const API_VERSION = 'v1';

@Injectable()
export class UsersService {
  constructor(
    @InjectRepository(User)
    private readonly usersRepository: Repository<User>,
  ) {}

  async findAll(): Promise<User[]> {
    return this.usersRepository.find();
  }

  async findOne(id: number): Promise<User> {
    return this.usersRepository.findOneOrFail({ where: { id } });
  }

  async create(dto: CreateUserDto): Promise<User> {
    const user = this.usersRepository.create(dto);
    return this.usersRepository.save(user);
  }

  async remove(id: number): Promise<void> {
    await this.usersRepository.delete(id);
  }
}

@Controller('users')
@UseGuards(JwtAuthGuard)
export class UsersController {
  constructor(private readonly usersService: UsersService) {}

  @Get()
  async findAll(): Promise<User[]> {
    return this.usersService.findAll();
  }

  @Get(':id')
  async findOne(@Param('id') id: string): Promise<User> {
    return this.usersService.findOne(+id);
  }

  @Post()
  async create(@Body() createUserDto: CreateUserDto): Promise<User> {
    return this.usersService.create(createUserDto);
  }

  @Delete(':id')
  async remove(@Param('id') id: string): Promise<void> {
    return this.usersService.remove(+id);
  }
}

@Module({
  controllers: [UsersController],
  providers: [UsersService],
})
export class UsersModule {}
"""

REACT_SAMPLE = """\
'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Avatar } from './Avatar';

export const AVATAR_SIZE = 48;

export interface ProfileProps {
  userId: string;
  onUpdate?: (data: unknown) => void;
}

export function UserProfile({ userId, onUpdate }: ProfileProps) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  useEffect(() => {
    setLoading(true);
    fetch(`/api/users/${userId}`)
      .then((r) => r.json())
      .then(setUser)
      .finally(() => setLoading(false));
  }, [userId]);

  const handleEdit = useCallback(() => {
    router.push(`/users/${userId}/edit`);
  }, [router, userId]);

  if (loading) return <div>Loading...</div>;
  return <Avatar user={user} size={AVATAR_SIZE} onEdit={handleEdit} />;
}

export const ProfileCard = ({ userId }: { userId: string }) => (
  <div>
    <UserProfile userId={userId} />
  </div>
);

export default UserProfile;
"""

VUE_SAMPLE = """\
<template>
  <div>{{ greeting }}</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const props = defineProps<{
  initialCount: number;
  title?: string;
}>();

const emit = defineEmits<{
  change: [count: number];
  submit: [value: string];
}>();

defineExpose({ reset });

const count = ref(props.initialCount);
const MAX_COUNT = 100;
const router = useRouter();
const auth = useAuthStore();

const greeting = computed(() => `Count: ${count.value}`);

function increment() {
  if (count.value < MAX_COUNT) {
    count.value++;
    emit('change', count.value);
  }
}

function reset() {
  count.value = props.initialCount;
}

onMounted(() => {
  console.log('mounted');
});
</script>
"""

PHP_SAMPLE = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\User;
use App\\Models\\Post;
use App\\Http\\Requests\\StoreUserRequest;
use Illuminate\\Http\\{Request, Response};
use Illuminate\\Support\\Facades\\Auth;

define('MAX_USERS', 1000);

class UserController extends Controller
{
    public function __construct(
        private readonly UserRepository $repository,
    ) {}

    public function index(Request $request): Response
    {
        $users = User::with('posts')->paginate(15);
        return response()->json($users);
    }

    public function store(StoreUserRequest $request): Response
    {
        $user = User::create($request->validated());
        return response()->json($user, 201);
    }

    public function destroy(int $id): Response
    {
        User::findOrFail($id)->delete();
        return response()->noContent();
    }
}

class User extends Model
{
    protected $fillable = ['name', 'email', 'password'];

    protected $casts = [
        'email_verified_at' => 'datetime',
    ];

    const STATUS_ACTIVE = 'active';

    public function posts()
    {
        return $this->hasMany(Post::class);
    }

    public function profile()
    {
        return $this->hasOne(Profile::class);
    }

    public function scopeActive($query)
    {
        return $query->where('status', self::STATUS_ACTIVE);
    }

    public function getFullNameAttribute(): string
    {
        return "{$this->first_name} {$this->last_name}";
    }

    public function setPasswordAttribute(string $value): void
    {
        $this->attributes['password'] = bcrypt($value);
    }
}
"""

PHP_ROUTES_SAMPLE = """\
<?php

use App\\Http\\Controllers\\UserController;
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::middleware('auth')->group(function () {
    Route::get('/users', [UserController::class, 'index']);
    Route::post('/users', [UserController::class, 'store']);
    Route::get('/users/{id}', [UserController::class, 'show']);
    Route::put('/users/{id}', [UserController::class, 'update']);
    Route::delete('/users/{id}', [UserController::class, 'destroy']);
    Route::resource('posts', PostController::class);
});
"""


@pytest.fixture
def python_file() -> FileNode:
    return FileNode(
        path="src/processor.py",
        name="processor.py",
        language=Language.PYTHON,
        size_bytes=len(PYTHON_SAMPLE),
        content=PYTHON_SAMPLE,
        sha="abc123",
    )


@pytest.fixture
def js_file() -> FileNode:
    return FileNode(
        path="src/api/client.js",
        name="client.js",
        language=Language.JAVASCRIPT,
        size_bytes=len(JS_SAMPLE),
        content=JS_SAMPLE,
        sha="def456",
    )


@pytest.fixture
def ts_file() -> FileNode:
    return FileNode(
        path="src/services/user.service.ts",
        name="user.service.ts",
        language=Language.TYPESCRIPT,
        size_bytes=len(TS_SAMPLE),
        content=TS_SAMPLE,
        sha="ghi789",
    )


@pytest.fixture
def nestjs_file() -> FileNode:
    return FileNode(
        path="src/users/users.controller.ts",
        name="users.controller.ts",
        language=Language.TYPESCRIPT,
        size_bytes=len(NESTJS_SAMPLE),
        content=NESTJS_SAMPLE,
        sha="nest001",
    )


@pytest.fixture
def react_file() -> FileNode:
    return FileNode(
        path="src/components/UserProfile.tsx",
        name="UserProfile.tsx",
        language=Language.TYPESCRIPT,
        size_bytes=len(REACT_SAMPLE),
        content=REACT_SAMPLE,
        sha="react001",
    )


@pytest.fixture
def vue_file() -> FileNode:
    return FileNode(
        path="src/components/Counter.vue",
        name="Counter.vue",
        language=Language.VUE,
        size_bytes=len(VUE_SAMPLE),
        content=VUE_SAMPLE,
        sha="vue001",
    )


@pytest.fixture
def php_file() -> FileNode:
    return FileNode(
        path="app/Http/Controllers/UserController.php",
        name="UserController.php",
        language=Language.PHP,
        size_bytes=len(PHP_SAMPLE),
        content=PHP_SAMPLE,
        sha="php001",
    )


@pytest.fixture
def php_routes_file() -> FileNode:
    return FileNode(
        path="routes/api.php",
        name="api.php",
        language=Language.PHP,
        size_bytes=len(PHP_ROUTES_SAMPLE),
        content=PHP_ROUTES_SAMPLE,
        sha="php002",
    )


@pytest.fixture
def sample_repo(python_file: FileNode, js_file: FileNode) -> RepoInfo:
    return RepoInfo(
        owner="testorg",
        repo_name="myproject",
        full_name="testorg/myproject",
        description="A test project",
        default_branch="main",
        url="https://github.com/testorg/myproject",
        clone_url="https://github.com/testorg/myproject.git",
        files=[python_file, js_file],
    )
